"""
Tests for matching USGS quakes to GDACS and EM-DAT events by time and distance.
"""

from unittest.mock import Mock

import pytest

from monty_tool.geo import quake_links


def _usgs(item_id, when, lon, lat, magnitude):
    return {
        'id': item_id, 'bbox': [lon, lat, lon, lat],
        'properties': {'datetime': when, 'eq:magnitude': magnitude},
    }


def _gdacs(item_id, when, lon, lat, codes=('GH0101',)):
    return {
        'id': item_id, 'bbox': [lon, lat, lon, lat],
        'properties': {'datetime': when, 'monty:hazard_codes': list(codes)},
    }


def _emdat(item_id, when, geometry, codes=('nat-geo-ear-gro',)):
    return {
        'id': item_id, 'geometry': geometry,
        'properties': {'datetime': when, 'monty:hazard_codes': list(codes)},
    }


def _point(lon, lat):
    return {'type': 'Point', 'coordinates': [lon, lat]}


def _square(west, south, east, north):
    return {'type': 'Polygon', 'coordinates': [[
        [west, south], [east, south], [east, north], [west, north], [west, south],
    ]]}


MAINSHOCK = _usgs('usgs-event-main', '2016-08-24T01:36:32Z', 13.19, 42.70, 6.2)


def test_quake_is_located_at_its_bbox_centre():
    """
    Verify a quake is placed at the centre of its bounding box, and skipped without one.
    """
    quake = quake_links.quake_from_item({
        'id': 'x', 'bbox': [10.0, 20.0, 12.0, 22.0],
        'properties': {'datetime': '2020-01-01T00:00:00Z', 'eq:magnitude': 6.0},
    })
    assert quake is not None
    assert (quake.latitude, quake.longitude, quake.magnitude) == (21.0, 11.0, 6.0)
    assert quake_links.quake_from_item({'id': 'x', 'properties': {'datetime': 'x'}}) is None


def test_gdacs_event_shares_the_hazard_event_and_episode():
    """
    Verify a GDACS hazard maps to the event with the same event and episode numbers.
    """
    assert quake_links.gdacs_event_id('gdacs-hazard-1576261-1745386') == 'gdacs-event-1576261-1745386'


@pytest.mark.parametrize(('when', 'lon', 'matched'), [
    ('2016-08-24T01:37:30Z', 13.30, True),   # 58 s and ~9 km away
    ('2016-08-24T01:45:00Z', 13.19, False),  # over 2 minutes later
    ('2016-08-24T01:36:40Z', 16.00, False),  # about 230 km away
])
def test_gdacs_match_needs_close_time_and_place(when, lon, matched):
    """
    Verify a GDACS quake matches only within 2 minutes and 100 km of the USGS quake.
    """
    links = quake_links.quake_event_links([MAINSHOCK], [_gdacs('gdacs-hazard-1-2', when, lon, 42.70)], [])
    assert links == ({'usgs-event-main': ['gdacs-event-1-2']} if matched else {})


def test_gdacs_non_quakes_are_ignored():
    """
    Verify GDACS hazards that are not quakes never match, however close.
    """
    flood = _gdacs('gdacs-hazard-1-2', '2016-08-24T01:36:32Z', 13.19, 42.70, codes=('FL',))
    assert quake_links.quake_event_links([MAINSHOCK], [flood], []) == {}


def test_emdat_point_matches_within_a_day_and_150_km():
    """
    Verify an EM-DAT point matches a quake within a day and 150 km, and not beyond.
    """
    near = _emdat('emdat-event-2016-0313-ITA', '2016-08-25T00:00:00Z', _point(13.30, 42.63))
    far = _emdat('emdat-event-far', '2016-08-24T00:00:00Z', _point(9.0, 45.5))
    late = _emdat('emdat-event-late', '2016-08-27T00:00:00Z', _point(13.30, 42.63))
    links = quake_links.quake_event_links([MAINSHOCK], [], [near, far, late])
    assert links == {'usgs-event-main': ['emdat-event-2016-0313-ITA']}


def test_emdat_polygon_matches_an_epicentre_inside_it():
    """
    Verify an EM-DAT province polygon matches a quake whose epicentre lies inside it.
    """
    province = _emdat('emdat-event-poly', '2016-08-24T00:00:00Z', _square(12.0, 41.0, 15.0, 44.0))
    assert quake_links.quake_event_links([MAINSHOCK], [], [province]) == {'usgs-event-main': ['emdat-event-poly']}


def test_emdat_goes_to_the_largest_nearby_quake():
    """
    Verify an EM-DAT quake links to the mainshock, not to a nearby aftershock.
    """
    aftershock = _usgs('usgs-event-after', '2016-08-24T02:33:28Z', 13.15, 42.79, 5.6)
    record = _emdat('emdat-event-2016-0313-ITA', '2016-08-24T00:00:00Z', _point(13.30, 42.63))
    links = quake_links.quake_event_links([aftershock, MAINSHOCK], [], [record])
    assert links == {'usgs-event-main': ['emdat-event-2016-0313-ITA']}


def test_emdat_links_every_quake_tied_for_largest():
    """
    Verify a doublet of equal-magnitude quakes both link to the EM-DAT record.
    """
    first = _usgs('usgs-event-a', '2023-10-07T06:41:03Z', 62.06, 34.61, 6.3)
    second = _usgs('usgs-event-b', '2023-10-07T07:12:49Z', 62.04, 34.57, 6.3)
    record = _emdat('emdat-event-2023-0636-AFG', '2023-10-07T00:00:00Z', _point(62.2, 34.35))
    links = quake_links.quake_event_links([first, second], [], [record])
    assert links == {'usgs-event-a': ['emdat-event-2023-0636-AFG'], 'usgs-event-b': ['emdat-event-2023-0636-AFG']}


def test_quakes_below_the_floor_are_left_out():
    """
    Verify quakes under `min_magnitude` are not matched at all.
    """
    small = _usgs('usgs-event-small', '2016-08-24T01:36:32Z', 13.19, 42.70, 4.0)
    record = _emdat('emdat-event-x', '2016-08-24T00:00:00Z', _point(13.19, 42.70))
    assert quake_links.quake_event_links([small], [], [record], min_magnitude=5.5) == {}


def test_emdat_records_without_geometry_or_quake_codes_are_skipped():
    """
    Verify EM-DAT records lacking geometry, or not tagged as quakes, never match.
    """
    no_shape = _emdat('emdat-event-a', '2016-08-24T00:00:00Z', None)
    flood = _emdat('emdat-event-b', '2016-08-24T00:00:00Z', _point(13.19, 42.70), codes=('nat-hyd-flo-flo',))
    assert quake_links.quake_event_links([MAINSHOCK], [], [no_shape, flood]) == {}


def test_fetch_emdat_quakes_retries_then_returns_items(monkeypatch):
    """
    Verify the EM-DAT quake search is retried after a failure and filters on quake codes.
    """
    monkeypatch.setattr(quake_links.time, 'sleep', lambda seconds: None)
    item = Mock()
    item.to_dict.return_value = {'id': 'emdat-event-x'}
    client = Mock()
    client.search.side_effect = [RuntimeError('timeout'), Mock(items=Mock(return_value=[item]))]

    assert list(quake_links.fetch_emdat_quakes(client)) == [{'id': 'emdat-event-x'}]
    assert client.search.call_count == 2
    query = client.search.call_args.kwargs['filter']
    assert query['op'] == 'a_overlaps' and 'nat-geo-ear-gro' in query['args'][1]


def test_fetch_emdat_quakes_gives_up_after_the_last_attempt(monkeypatch):
    """
    Verify the EM-DAT quake search raises once every attempt has failed.
    """
    monkeypatch.setattr(quake_links.time, 'sleep', lambda seconds: None)
    client = Mock()
    client.search.side_effect = RuntimeError('down')
    with pytest.raises(RuntimeError, match='down'):
        list(quake_links.fetch_emdat_quakes(client, attempts=2))
