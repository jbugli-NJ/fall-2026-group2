"""
Tests for CEMS footprints.
"""

from unittest.mock import Mock

import pytest
import requests

from monty_tool.geo import cems

BUCKET = 'https://rapidmapping-viewer.s3.eu-west-1.amazonaws.com/EMSR935/AOI01/DEL_PRODUCT'


def _item(**overrides):
    item = {
        'id': 'cems-response-EMSR935-aoi01-del',
        'properties': {
            'title': 'CEMS delineation product — Moraira (EMSR935)',
            'datetime': '2026-10-20T23:59:40Z',
            'monty:hazard_codes': ['MH0604', 'FL', 'nat-hyd-flo-riv'],
            'monty:country_codes': ['ESP'],
            'monty:response_detail': {
                'type': 'eo-del', 'status': 'published', 'source_id': 'EMSR935',
            },
        },
        'assets': {
            'observedEventA': {'href': f'{BUCKET}/observedEventA_v1.json'},
            'floodDepthA': {'href': f'{BUCKET}/floodDepthA_v1.json'},
            'floodDepthA_style': {'href': f'{BUCKET}/floodDepthA_v1.sld'},
            'notAnalysedA': {'href': f'{BUCKET}/notAnalysedA_v1.json'},
            'basemap': {'href': f'{BUCKET}/ortho_cog.tif'},
            'download': {'href': f'{BUCKET}/product.zip'},
        },
        'links': [
            {'rel': 'derived_from', 'href': 'https://rapidmapping.emergency.copernicus.eu/EMSR935'},
        ],
    }
    item.update(overrides)
    return item


def _square(size_degrees):
    return {
        'type': 'FeatureCollection',
        'features': [{
            'type': 'Feature',
            'properties': {},
            'geometry': {
                'type': 'Polygon',
                'coordinates': [[
                    [0, 0], [size_degrees, 0], [size_degrees, size_degrees],
                    [0, size_degrees], [0, 0],
                ]],
            },
        }],
    }


def test_item_yields_one_footprint_per_known_layer():
    """
    Confirms footprint layers are kept and styles, imagery, downloads and
    not-analysed areas are not.
    """
    footprints = cems.footprints_from_item(_item())

    assert [(f.layer, f.detail, f.severity_property) for f in footprints] == [
        ('observed_extent', 'mapped', 'notation'),
        ('water_depth', 'modelled', 'value'),
    ]
    first = footprints[0]
    assert first.event_ref == 'EMSR935'
    assert first.area_name == 'Moraira'
    assert first.country_codes == ['ESP']
    assert first.source_url == 'https://rapidmapping.emergency.copernicus.eu/EMSR935'
    assert first.geojson is None


def test_withdrawn_product_yields_nothing():
    """
    Confirms products CEMS has withdrawn are skipped.
    """
    item = _item()
    item['properties']['monty:response_detail']['status'] = 'withdrawn'

    assert cems.footprints_from_item(item) == []


def test_monitoring_layer_keys_are_normalised():
    """
    Confirms monitoring products' prefixed asset keys map to the base layer.
    """
    item = _item(assets={
        'EMSR933_AOI03_DEL_MONIT02_maximumFloodExtentA': {
            'href': f'{BUCKET}/EMSR933_AOI03_DEL_MONIT02_maximumFloodExtentA_v1.json',
        },
    })

    [footprint] = cems.footprints_from_item(item)

    assert footprint.layer == 'maximum_extent'


def test_activation_falls_back_to_item_id():
    """
    Confirms the activation ID is read from the Item ID when the response
    detail lacks it.
    """
    item = _item()
    del item['properties']['monty:response_detail']['source_id']

    assert cems.footprints_from_item(item)[0].event_ref == 'EMSR935'


def test_load_geojson_fills_in_size(monkeypatch):
    """
    Confirms a downloaded layer gets its features, count and size.
    """
    geojson = _square(0.1)
    monkeypatch.setattr(
        cems.requests, 'get', Mock(return_value=Mock(json=Mock(return_value=geojson))),
    )
    footprint = cems.footprints_from_item(_item())[0]

    loaded = cems.load_geojson(footprint)

    assert loaded.geojson == geojson
    assert loaded.feature_count == 1
    assert loaded.size_km == pytest.approx(11.1, abs=0.1)
    assert loaded.detail == 'mapped'


def test_get_cems_footprints_keeps_layers_that_fail_to_download(monkeypatch):
    """
    Confirms one failed download does not drop the other footprints.
    """
    client = Mock()
    client.search.return_value.items_as_dicts.return_value = [_item()]
    responses = [
        Mock(json=Mock(return_value=_square(0.1))),
        requests.ConnectionError('bucket unavailable'),
    ]
    monkeypatch.setattr(cems.requests, 'get', Mock(side_effect=responses))

    footprints = cems.get_cems_footprints('EMSR935', client=client)

    assert [f.geojson is not None for f in footprints] == [True, False]
    search_filter = client.search.call_args.kwargs['filter']
    assert search_filter['args'][1] == 'cems-response-EMSR935-%'


def test_get_cems_footprints_rejects_bad_activation():
    """
    Confirms a malformed activation ID is rejected before searching.
    """
    with pytest.raises(ValueError):
        cems.get_cems_footprints('935', client=Mock())



def test_item_without_activation_yields_nothing():
    """
    Confirms an Item with no activation ID in its detail or its ID is skipped.
    """
    item = _item(id='cems-response-unknown')
    del item['properties']['monty:response_detail']['source_id']

    assert cems.footprints_from_item(item) == []


def test_get_cems_footprints_without_download(monkeypatch):
    """
    Confirms download=False returns footprints without fetching any layer.
    """
    client = Mock()
    client.search.return_value.items_as_dicts.return_value = [_item()]
    get = Mock()
    monkeypatch.setattr(cems.requests, 'get', get)

    footprints = cems.get_cems_footprints('EMSR935', download=False, client=client)

    assert len(footprints) == 2
    assert all(f.geojson is None for f in footprints)
    get.assert_not_called()
