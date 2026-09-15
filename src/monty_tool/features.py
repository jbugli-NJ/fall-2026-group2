"""
Feature engineering over flattened Montandon Items.

Every function takes and returns a Polars DataFrame produced by
`monty_tool.data_cache.items_to_frame`, adding columns without
removing any.
"""

# Imports

import re
from importlib.resources import files

import polars as pl


# Resources

HAZARD_PROFILES_PATH = files('monty_tool.resources') / 'hazard_profiles.csv'

# Source-data typos seen in the wild, mapped to the code the crosswalk knows.
HAZARD_CODE_FIXUPS = {
    'nat-gem-ear-gro': 'nat-geo-ear-gro',
}

# Every vocabulary column in the crosswalk that can appear in `monty:hazard_codes`.
HAZARD_CODE_COLUMNS = ('undrr_2025_key', 'undrr_key', 'glide_code', 'emdat_key')

# `monty:impact_detail.type` values worth pivoting into their own columns.
IMPACT_TYPES = (
    'death',
    'injured',
    'affected_total',
    'displaced_total',
    'displaced_internal',
    'cost',
)

_HTML_TAG = re.compile(r'<[^>]+>')
_WHITESPACE = re.compile(r'\s+')


# Hazard codes

def load_hazard_profiles() -> pl.DataFrame:
    """
    Load the vendored hazard-code crosswalk.
    """
    return pl.read_csv(str(HAZARD_PROFILES_PATH))


def hazard_code_lookup() -> pl.DataFrame:
    """
    One row per hazard code across every vocabulary, with its canonical
    cluster and family. Codes that appear under several vocabularies keep
    the first mapping.
    """
    profiles = load_hazard_profiles()
    stacked = pl.concat([
        profiles.select(
            pl.col(column).alias('hazard_code'),
            pl.col('label').alias('hazard_label'),
            pl.col('cluster_label').alias('hazard_cluster'),
            pl.col('family_label').alias('hazard_family'),
        ).drop_nulls('hazard_code')
        for column in HAZARD_CODE_COLUMNS
    ])
    return stacked.unique('hazard_code', keep='first')


def add_hazard_features(frame: pl.DataFrame) -> pl.DataFrame:
    """
    Add `hazard_cluster`, `hazard_family`, and `hazard_label` from the first
    code in `monty:hazard_codes` that the crosswalk recognises, after
    applying known typo fixups. Also adds `hazard_code_primary`, the code
    that was matched, and `n_hazard_codes`.
    """
    lookup = hazard_code_lookup()
    exploded = (
        frame.select('id', 'monty:hazard_codes')
        .with_row_index('_row')
        .explode('monty:hazard_codes')
        .with_columns(
            pl.col('monty:hazard_codes')
            .replace(HAZARD_CODE_FIXUPS)
            .alias('hazard_code_primary')
        )
        .join(lookup, left_on='hazard_code_primary', right_on='hazard_code', how='left')
        .filter(pl.col('hazard_cluster').is_not_null())
        .unique('id', keep='first', maintain_order=True)
        .select('id', 'hazard_code_primary', 'hazard_label', 'hazard_cluster', 'hazard_family')
    )
    return (
        frame.join(exploded, on='id', how='left')
        .with_columns(pl.col('monty:hazard_codes').list.len().alias('n_hazard_codes'))
    )


# Time

def add_temporal_features(frame: pl.DataFrame) -> pl.DataFrame:
    """
    Add `start`, `end` (as datetimes), `year`, `month`, and `duration_days`.
    """
    return frame.with_columns(
        pl.col('start_datetime').str.to_datetime(time_zone='UTC').alias('start'),
        pl.col('end_datetime').str.to_datetime(time_zone='UTC').alias('end'),
    ).with_columns(
        pl.col('start').dt.year().alias('year'),
        pl.col('start').dt.month().alias('month'),
        (pl.col('end') - pl.col('start')).dt.total_days().alias('duration_days'),
    )


# Space

def add_spatial_features(frame: pl.DataFrame) -> pl.DataFrame:
    """
    Add bbox-derived `centroid_lon`, `centroid_lat`, `bbox_area_deg2`, and
    `n_countries`. Works on Items pulled without geometry.
    """
    bbox = pl.col('bbox')
    return frame.with_columns(
        ((bbox.list.get(0) + bbox.list.get(2)) / 2).alias('centroid_lon'),
        ((bbox.list.get(1) + bbox.list.get(3)) / 2).alias('centroid_lat'),
        ((bbox.list.get(2) - bbox.list.get(0)) * (bbox.list.get(3) - bbox.list.get(1))).alias('bbox_area_deg2'),
        pl.col('monty:country_codes').list.len().alias('n_countries'),
    )


# Impacts

def impact_summary(impacts: pl.DataFrame) -> pl.DataFrame:
    """
    Pivot impact Items to one row per `monty:corr_id` with a column per
    impact type in `IMPACT_TYPES` (summed), plus `n_impact_records`.
    """
    return (
        impacts.filter(pl.col('monty:impact_detail.type').is_in(IMPACT_TYPES))
        .group_by('monty:corr_id', 'monty:impact_detail.type')
        .agg(pl.col('monty:impact_detail.value').sum())
        .pivot(on='monty:impact_detail.type', index='monty:corr_id', values='monty:impact_detail.value')
        .join(
            impacts.group_by('monty:corr_id').agg(pl.len().alias('n_impact_records')),
            on='monty:corr_id',
            how='full',
            coalesce=True,
        )
    )


def event_feature_table(events: pl.DataFrame, impacts: pl.DataFrame | None = None) -> pl.DataFrame:
    """
    Apply every feature function to an events frame and, if given, join
    the summarised impacts on `monty:corr_id`.
    """
    table = add_spatial_features(add_temporal_features(add_hazard_features(events)))
    if impacts is not None:
        table = table.join(impact_summary(impacts), on='monty:corr_id', how='left')
    return table


# Text

def strip_html(text: str | None) -> str | None:
    """
    Remove HTML tags and collapse whitespace; `NA` and blanks become None.
    """
    if text is None:
        return None
    cleaned = _WHITESPACE.sub(' ', _HTML_TAG.sub(' ', text)).strip()
    return cleaned if cleaned and cleaned != 'NA' else None


def add_clean_description(frame: pl.DataFrame) -> pl.DataFrame:
    """
    Add `description_clean` (HTML stripped, placeholders nulled) and its
    character length `description_len`.
    """
    return frame.with_columns(
        pl.col('description').map_elements(strip_html, return_dtype=pl.String).alias('description_clean')
    ).with_columns(
        pl.col('description_clean').str.len_chars().alias('description_len')
    )
