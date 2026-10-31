"""
Tests for USGS ShakeMap footprints.
"""

from unittest.mock import Mock

import pytest
from shapely.geometry import shape

from monty_tool.geo import usgs

GRID_URL = 'https://earthquake.usgs.gov/product/shakemap/us1/coverage_mmi_medium_res.covjson'

SOURCE_URL = 'https://earthquake.usgs.gov/fdsnws/event/1/query?eventid=us1&format=geojson'


def _coverage(values, columns=3, rows=3):
    """
    A CoverageJSON MMI grid with 1-degree cells starting at (10, 20).
    """
    return {
        'type': 'Coverage',
        'domain': {'axes': {
            'x': {'start': 10.0, 'stop': 10.0 + columns - 1, 'num': columns},
            'y': {'start': 20.0, 'stop': 20.0 + rows - 1, 'num': rows},
        }},
        'ranges': {'MMI': {'values': values}},
    }


def _event(shakemaps):
    return {
        'id': 'us1',
        'properties': {
            'time': 1_760_000_000_000,
            'place': '10 km N of Somewhere',
            'url': 'https://earthquake.usgs.gov/earthquakes/eventpage/us1',
            'products': {'shakemap': shakemaps},
        },
    }


def _shakemap(weight, version, grid=True):
    contents = {usgs.GRID_CONTENT: {'url': f'{GRID_URL}?v={version}'}} if grid else {}
    return {
        'preferredWeight': weight,
        'properties': {'version': version, 'map-status': 'reviewed'},
        'contents': contents,
    }


def _item(item_id='usgs-event-us1', source=True):
    assets = {'source': {'href': SOURCE_URL}} if source else {}
    return {
        'id': item_id,
        'properties': {
            'monty:hazard_codes': ['GH0101', 'EQ'],
            'monty:country_codes': ['JPN'],
        },
        'assets': assets,
    }


def _client(items):
    """
    A STAC client whose searches return `items`.
    """
    client = Mock()
    client.search.return_value.items_as_dicts.return_value = iter(items)
    return client


@pytest.mark.parametrize(('value', 'band'), [
    (5.49, 5), (5.5, 6), (6.49, 6), (0.2, 1), (12.0, 10),
])
def test_mmi_band_rounds_like_usgs(value, band):
    """
    Confirms grid values round to bands at the half, clamped to I-X.
    """
    assert usgs.mmi_band(value) == band


def test_grid_to_geojson_groups_cells_into_bands():
    """
    Confirms each band becomes one feature, strongest first, covering
    exactly its cells, and that missing values are skipped.
    """
    values = [
        4.2, 4.0, None,
        4.1, 7.0, 4.3,
        4.4, 4.1, 3.9,
    ]

    features = usgs.grid_to_geojson(_coverage(values))['features']

    assert [f['properties']['intensity'] for f in features] == ['VII', 'IV']
    strong, light = (shape(f['geometry']) for f in features)
    assert strong.area == pytest.approx(1.0)
    assert light.area == pytest.approx(7.0)
    assert strong.bounds == pytest.approx((10.5, 20.5, 11.5, 21.5))
    assert features[0]['properties']['shaking'] == 'Very strong'


def test_grid_to_geojson_drops_bands_below_minimum():
    """
    Confirms bands below min_mmi are left out.
    """
    values = [4.0, 5.0, 6.0, 4.0]

    features = usgs.grid_to_geojson(_coverage(values, columns=2, rows=2), min_mmi=5)['features']

    assert [f['properties']['mmi'] for f in features] == [6, 5]


def test_footprint_uses_preferred_shakemap():
    """
    Confirms the highest-weighted ShakeMap is used.
    """
    footprint = usgs.footprint_from_event(
        _event([_shakemap(10, '2'), _shakemap(200, '5')]), _item(),
    )

    assert footprint is not None
    assert footprint.data_url.endswith('v=5')
    assert footprint.item_id == 'usgs-event-us1'
    assert footprint.event_ref == 'us1'
    assert footprint.hazard_codes == ['GH0101', 'EQ']
    assert footprint.country_codes == ['JPN']
    assert footprint.label == 'Shaking intensity (ShakeMap v5, reviewed)'
    assert footprint.detail == 'modelled'
    assert footprint.datetime.year == 2025


@pytest.mark.parametrize('shakemaps', [[], [_shakemap(10, '1', grid=False)]])
def test_footprint_is_none_without_shakemap_grid(shakemaps):
    """
    Confirms events without a ShakeMap, or without its grid, give None.
    """
    assert usgs.footprint_from_event(_event(shakemaps), _item()) is None


def test_get_item_searches_the_matching_collection():
    """
    Confirms event and hazard IDs are looked up in their own collection.
    """
    client = _client([_item('usgs-hazard-us1-shakemap')])

    item = usgs.get_item('usgs-hazard-us1-shakemap', client=client)

    assert item is not None
    assert client.search.call_args.kwargs == {
        'collections': ['usgs-hazards'], 'ids': ['usgs-hazard-us1-shakemap'],
    }


def test_get_item_rejects_non_montandon_ids():
    """
    Confirms a bare USGS ID is refused rather than fetched from USGS.
    """
    with pytest.raises(ValueError, match='Not a Montandon USGS item ID'):
        usgs.get_item('us1', client=_client([]))


def test_get_source_event_needs_source_asset():
    """
    Confirms an Item without a source asset raises rather than guessing.
    """
    with pytest.raises(ValueError, match='no source asset'):
        usgs.get_source_event(_item(source=False))


def test_get_shakemap_footprints_follows_source_asset(monkeypatch):
    """
    Confirms the USGS event is fetched from the Item's source asset and its
    grid turned into bands.
    """
    get = Mock(side_effect=[
        Mock(json=Mock(return_value=_event([_shakemap(10, '1')]))),
        Mock(json=Mock(return_value=_coverage([6.0] * 9))),
    ])
    monkeypatch.setattr(usgs.requests, 'get', get)

    [footprint] = usgs.get_shakemap_footprints('usgs-event-us1', client=_client([_item()]))

    assert get.call_args_list[0].args == (SOURCE_URL,)
    assert footprint.feature_count == 1
    assert footprint.size_km == pytest.approx(3 * 110.57, abs=0.1)


def test_get_shakemap_footprints_skips_quakes_missing_from_montandon(monkeypatch):
    """
    Confirms a quake Montandon lacks gives an empty list without asking USGS.
    """
    get = Mock()
    monkeypatch.setattr(usgs.requests, 'get', get)

    assert usgs.get_shakemap_footprints('usgs-event-us1', client=_client([])) == []
    get.assert_not_called()


def test_get_shakemap_footprints_without_shakemap(monkeypatch):
    """
    Confirms a quake with no ShakeMap gives an empty list without a grid fetch.
    """
    get = Mock(return_value=Mock(json=Mock(return_value=_event([]))))
    monkeypatch.setattr(usgs.requests, 'get', get)

    assert usgs.get_shakemap_footprints('usgs-event-us1', client=_client([_item()])) == []
    assert get.call_count == 1
