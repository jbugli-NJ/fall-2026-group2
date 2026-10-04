"""
Tests for the network setup tool.
"""

import gzip
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

from botocore.exceptions import ClientError
import pytest

from monty_tool.tools import setup_network
from monty_tool.tools.resources import MONTANDON_NODE_DATA_BUCKET_PREFIX
from monty_tool.weather.schemas import WeatherResult


@pytest.fixture
def weather_json():
    """
    Provide one valid result without measurements.
    """
    return WeatherResult(
        item_id='event-1', latitude=10, longitude=20,
        start_date=date(2024, 1, 1), end_date=date(2024, 1, 3),
    ).model_dump_json()


def test_montandon_node_data_keys_lists_gzip_files_only():
    """
    Select and sort gzip files under the Montandon prefix.
    """
    prefix = MONTANDON_NODE_DATA_BUCKET_PREFIX
    bucket = Mock()
    bucket.objects.filter.return_value = [
        SimpleNamespace(key=prefix + name) for name in ('z.jsonl.gz', 'notes.txt', 'a.jsonl.gz')
    ]
    assert setup_network._montandon_node_data_keys(bucket) == [prefix + 'a.jsonl.gz', prefix + 'z.jsonl.gz']
    bucket.objects.filter.assert_called_once_with(Prefix=prefix)


@pytest.mark.parametrize('failure', ['json', 'duplicate', 'missing'])
def test_invalid_weather_preserves_graph(monkeypatch, weather_json, failure):
    """
    Reject bad or missing weather before clearing the graph.
    """
    def download(bucket, key, path):
        """
        Supply the selected invalid input.
        """
        if failure == 'missing':
            raise ClientError({'Error': {'Code': 'NoSuchKey'}}, 'GetObject')
        contents = 'not json' if failure == 'json' else weather_json + '\n' + weather_json
        with gzip.open(path, 'wt') as file:
            file.write(contents)

    clear = Mock()
    monkeypatch.setattr(setup_network, 'get_bucket', Mock())
    monkeypatch.setattr(setup_network, '_montandon_node_data_keys', Mock(return_value=['nodes.jsonl.gz']))
    monkeypatch.setattr(setup_network, 'download_object', download)
    monkeypatch.setattr(setup_network, 'clear_db', clear)
    message = 's3://' if failure == 'missing' else 'line'
    with pytest.raises(ValueError, match=message):
        setup_network.main()
    clear.assert_not_called()


def test_weather_batches_are_bounded(monkeypatch, tmp_path, weather_json):
    """
    Insert five records in batches of two, two, and one.
    """
    path = tmp_path / 'weather.jsonl.gz'
    with gzip.open(path, 'wt') as file:
        file.write('\n'.join(weather_json.replace('event-1', f'event-{i}') for i in range(5)))
    insert = Mock(side_effect=lambda driver, batch: len(batch))
    monkeypatch.setattr(setup_network, 'get_graph_db_driver', MagicMock())
    monkeypatch.setattr(setup_network, 'NETWORK_INSERT_BATCH_SIZE', 2)
    monkeypatch.setattr(setup_network, 'insert_weather_properties', insert)
    setup_network._insert_weather_data(path)
    assert [len(call.args[1]) for call in insert.call_args_list] == [2, 2, 1]

def test_setup_loads_news_after_events(monkeypatch):
    bucket = Mock()
    driver_context = MagicMock()
    driver = driver_context.__enter__.return_value
    order = []

    def record_events(*_args):
        order.append("events")

    def record_news(*_args, **_kwargs):
        order.append("news")
        return 2

    insert_events = Mock(side_effect=record_events)
    load_news = Mock(side_effect=record_news)

    monkeypatch.setattr(setup_network, "get_env_bucket_name", Mock(return_value="test-bucket"))
    monkeypatch.setattr(setup_network, "get_bucket", Mock(return_value=bucket))
    monkeypatch.setattr(setup_network, "_montandon_node_data_keys", Mock(return_value=["nodes.jsonl.gz"]))
    monkeypatch.setattr(setup_network, "_download_validated_weather", Mock())
    monkeypatch.setattr(setup_network, "_download_node_data", Mock(side_effect=[["event"], ["appeal"]]))
    monkeypatch.setattr(setup_network, "get_graph_db_driver", Mock(return_value=driver_context))
    monkeypatch.setattr(setup_network, "insert_go_records_into_graph_db", insert_events)
    monkeypatch.setattr(setup_network, "load_news_into_graph", load_news)

    for name in (
        "clear_db",
        "initialize_db",
        "_insert_montandon_node_data",
        "_insert_weather_data",
        "initialize_vector_indexes",
        "create_montandon_deterministic_relationships",
        "create_montandon_similarity_relationships",
    ):
        monkeypatch.setattr(setup_network, name, Mock())

    setup_network.main()

    assert order == ["events", "news"]
    insert_events.assert_called_once_with(["event"], ["appeal"])
    load_news.assert_called_once_with(
        bucket, bucket_name="test-bucket", driver=driver
    )
