"""
Build an H3-indexed training frame: one row per (Montandon event, H3
cell), joined to per-event impact totals.

Two event sources are supported, and building both lets them be
compared on the same footing:

- `ifrc`: `ifrcevent-events` carry national-boundary polygons and link
  to IFRC GO operational records, but only a few hundred events have
  impact labels.
- `gdacs`: `gdacs-events` are points (k-ring footprint) with far more
  reported impact labels, especially for floods, but no operational
  signal and no epidemics.

Events link to their impacts on `monty:corr_id`, which
`monty_tool.features.impact_summary` pivots into per-event totals.
"""

# Imports

import argparse
from pathlib import Path
from typing import NamedTuple

import polars as pl

from monty_tool.data_cache import items_to_frame, load_collection
from monty_tool.features import impact_summary
from monty_tool.spatial import fill_events_with_h3


# Resources

class Source(NamedTuple):
    events: str
    impacts: str
    # Whether each collection was cached with geometry; see
    # `data_cache.raw_cache_path`. Impacts only need `monty:corr_id`
    # and `monty:impact_detail`, so the smaller no-geometry pull is
    # preferred where it exists.
    events_geometry: bool = True
    impacts_geometry: bool = True


SOURCES = {
    'ifrc': Source(events='ifrcevent-events', impacts='ifrcevent-impacts'),
    'gdacs': Source(events='gdacs-events', impacts='gdacs-impacts', impacts_geometry=False),
}

H3_RESOLUTION = 6

OUTPUT_DIR = Path('data/interim')

# `ifrcevent-events` geometries are country-scale, so one event can
# expand to hundreds of thousands of H3 cells at resolution 6 (~47M
# rows across the cache). Building one Python dict per row for every
# event at once is what makes that expensive, so events are filled in
# batches and each batch is folded into Polars right away.
EVENT_BATCH_SIZE = 25


# Pipeline steps

def output_path(source: str) -> Path:
    return OUTPUT_DIR / f'training_frame_h3_{source}.parquet'


def load_events_with_geometry(collection_id: str, geometry: bool = True) -> list[dict]:
    """
    Read cached raw Items into the flat dict shape `fill_events_with_h3`
    expects, keyed by `monty:corr_id` so they join onto impact totals.
    """
    events = []
    for item in load_collection(collection_id, geometry=geometry):
        properties = item.get('properties') or {}
        events.append({
            'event_id': properties.get('monty:corr_id'),
            'geometry': item.get('geometry'),
            'hazard_codes': properties.get('monty:hazard_codes'),
            'country_codes': properties.get('monty:country_codes'),
            'start_datetime': properties.get('start_datetime'),
        })
    return events


def load_impact_totals(collection_id: str, geometry: bool = True) -> pl.DataFrame:
    """
    Per-`monty:corr_id` impact totals via `monty_tool.features.impact_summary`.
    """
    impacts = items_to_frame(load_collection(collection_id, geometry=geometry))
    return impact_summary(impacts)


def _batched(items: list, size: int):
    for start in range(0, len(items), size):
        yield items[start:start + size]


def events_to_h3_frame(events: list[dict], resolution: int = H3_RESOLUTION) -> pl.DataFrame:
    """
    `fill_events_with_h3`, batched into Polars frames as it goes so
    memory stays bounded on a large event list.
    """
    batches = []
    for batch in _batched(events, EVENT_BATCH_SIZE):
        rows = fill_events_with_h3(batch, resolution=resolution)
        if rows:
            batches.append(pl.DataFrame(rows, infer_schema_length=None, strict=False))
    return pl.concat(batches, how='vertical_relaxed')


def add_impact_labels(frame: pl.DataFrame, impacts: pl.DataFrame) -> pl.DataFrame:
    """
    Left-join impact totals onto the H3 frame and add
    `log_affected = log(affected_total + 1)`, with missing totals as 0.
    """
    return (
        frame.join(impacts, left_on='event_id', right_on='monty:corr_id', how='left')
        .with_columns((pl.col('affected_total').fill_null(0) + 1).log().alias('log_affected'))
    )


def build_training_frame(source: str, resolution: int = H3_RESOLUTION) -> pl.DataFrame:
    """
    Assemble the H3-indexed training frame for one source in `SOURCES`.
    """
    config = SOURCES[source]
    events = load_events_with_geometry(config.events, geometry=config.events_geometry)
    frame = events_to_h3_frame(events, resolution=resolution)
    impacts = load_impact_totals(config.impacts, geometry=config.impacts_geometry)
    frame = add_impact_labels(frame, impacts)

    # TODO: join Kontur population-density rasters (data/exposure/) per
    # H3 cell here, once a raster -> H3 cell aggregation helper exists
    # in monty_tool.spatial (e.g. population_by_h3_cell).

    return frame


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--source', nargs='+', choices=list(SOURCES), default=list(SOURCES))
    parser.add_argument('--resolution', type=int, default=H3_RESOLUTION)
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for source in args.source:
        frame = build_training_frame(source, resolution=args.resolution)
        path = output_path(source)
        frame.write_parquet(path)
        print(f'[{source}] wrote {frame.height} rows x {frame.width} columns to {path}')


if __name__ == '__main__':
    main()
