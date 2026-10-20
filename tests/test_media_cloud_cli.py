"""Offline CLI migration tests: no publisher, Media Cloud, Qwen, or AWS calls."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from monty_tool.news import cli, runner
from monty_tool.news.collector import MediaCloudCandidateBatch
from monty_tool.news.schemas import MediaCloudStory
from monty_tool.news.s3_storage import read_media_cloud_snapshot


def record(item_id: str = "event-a") -> dict:
    return {
        "id": item_id, "collection": "emdat-events", "links": [],
        "bbox": [130.0, 30.0, 140.0, 40.0], "geometry": None,
        "properties": {
            "roles": ["source", "event"], "title": "Earthquake in Japan",
            "description": "A test earthquake record.", "keywords": ["earthquake"],
            "datetime": "2025-09-10T00:00:00Z",
            "start_datetime": "2025-09-10T00:00:00Z",
            "end_datetime": "2025-09-10T00:00:00Z",
            "monty:corr_id": item_id, "monty:country_codes": ["JPN"],
            "monty:hazard_codes": ["EQ"], "monty:src_event_id": item_id,
            "monty:episode_number": 1,
        },
    }


@pytest.fixture
def environment(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "load_dotenv", Mock())
    monkeypatch.delenv("AWS_BUCKET", raising=False)
    monkeypatch.delenv("AWS_BUCKET_PREFIX", raising=False)
    monkeypatch.setenv("MEDIACLOUD_API_KEY", "offline-test-value")
    load = Mock(side_effect=lambda *_a, **_k: iter([record()]))
    monkeypatch.setattr(cli, "load_collection", load)
    bucket = Mock()
    get_bucket = Mock(return_value=bucket)
    monkeypatch.setattr(cli, "get_bucket", get_bucket)
    download = Mock()
    monkeypatch.setattr(cli, "download_object", download)
    model = Mock(side_effect=AssertionError("Unexpected model load"))
    monkeypatch.setattr(cli, "load_qwen_judge", model)
    pause = Mock()
    monkeypatch.setattr(cli, "sleep", pause)
    collect = Mock(return_value=MediaCloudCandidateBatch(
        stories=[
            MediaCloudStory(id=1, title="Earthquake relief", url="https://example.com/one"),
            MediaCloudStory(id=2, title="Other report", url="https://example.com/two"),
        ], pages_fetched=1, next_page_token="more", duplicates_removed=0,
    ))
    monkeypatch.setattr(runner, "collect_media_cloud_candidates", collect)
    fetch = Mock(side_effect=[("<html>test</html>", "https://example.com/one"), None])
    monkeypatch.setattr(runner, "fetch_publisher_html", fetch)
    monkeypatch.setattr(runner, "extract_publisher_body", Mock(return_value="Earthquake relief in Japan."))
    arguments = [
        "--collection", "emdat-events", "--cache-dir", str(tmp_path / "raw"),
        "--no-geometry", "--start-date", "2025-09-01", "--end-date", "2025-09-30",
        "--media-collection-id", "123", "--output-dir", str(tmp_path / "results"),
    ]
    return arguments, load, collect, model, get_bucket, bucket, download, pause


EXECUTE = ["--execute", "--request-limit", "1", "--max-body-attempts", "2", "--defer-verification"]


def test_local_dry_run_never_collects_loads_model_or_uses_s3(environment, tmp_path, capsys):
    args, _, collect, model, get_bucket, _, _, _ = environment
    assert cli.main([*args, "--dry-run"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["provider"] == "media_cloud"
    assert plan["planned_jobs"] == 1
    assert plan["jobs"][0]["collection_ids"] == [123]
    assert plan["jobs"][0]["query"]["query"] == "Earthquake AND Japan AND language:en"
    assert plan["jobs"][0]["query"]["from_date"] == "2025-09-09"
    assert plan["media_cloud_requests_made"] == 0
    collect.assert_not_called()
    model.assert_not_called()
    get_bucket.assert_not_called()
    assert not (tmp_path / "results").exists()


def test_execute_saves_body_and_unavailable_candidate(environment, capsys):
    args, _, collect, model, get_bucket, _, _, _ = environment
    assert cli.main([*args, *EXECUTE]) == 0
    output = json.loads(capsys.readouterr().out)
    saved = output["saved"][0]
    restored = read_media_cloud_snapshot(Path(saved["snapshot"]))
    assert saved["decisions"] == {"uncertain": 2}
    assert saved["verification_mode"] == "deferred"
    assert saved["has_more_pages"] is True
    assert restored.assessments[0].evidence.body_text == "Earthquake relief in Japan."
    assert "deferred" in restored.assessments[0].decision.reason
    assert restored.assessments[1].evidence.story.url == "https://example.com/two"
    assert restored.assessments[1].evidence.body_text is None
    assert output["requests_made"] == 1
    assert collect.call_args.kwargs["max_pages"] == 1
    model.assert_not_called()
    get_bucket.assert_not_called()


@pytest.mark.parametrize("flags", [
    [], ["--execute"],
    ["--execute", "--request-limit", "1", "--max-body-attempts", "0"],
    ["--dry-run", "--page-size", "101"],
    ["--dry-run", "--s3-upload"],
    ["--dry-run", "--refresh-hours", "24"],
    ["--dry-run", "--max-pages", "0"],
    ["--dry-run", "--start-date", "2025-10-01"],
])
def test_invalid_arguments_fail_before_io(environment, flags):
    args, load, collect, model, get_bucket, _, _, _ = environment
    with pytest.raises(SystemExit) as error:
        cli.main([*args, *flags])
    assert error.value.code == 2
    load.assert_not_called()
    collect.assert_not_called()
    model.assert_not_called()
    get_bucket.assert_not_called()


def test_exact_event_selection_and_missing_id(environment, capsys):
    args, load, *_ = environment
    load.side_effect = lambda *_a, **_k: iter([record(), record("event-b")])
    assert cli.main([*args, "--dry-run", "--event-id", "event-b"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert [job["event"]["item_id"] for job in plan["jobs"]] == ["event-b"]
    with pytest.raises(SystemExit):
        cli.main([*args, "--dry-run", "--event-id", "missing"])


def test_s3_raw_input_uses_explicit_configuration(environment, monkeypatch, capsys):
    args, load, collect, _, get_bucket, bucket, download, _ = environment
    args = [*args[:2], *args[4:]]  # Remove only --cache-dir and its value.
    monkeypatch.setenv("AWS_BUCKET", "test-bucket")
    monkeypatch.setenv("AWS_BUCKET_PREFIX", " /test-owner/ ")
    assert cli.main([*args, "--dry-run"]) == 0
    get_bucket.assert_called_once_with("test-bucket")
    assert download.call_args.args[:2] == (bucket, "test-owner/raw/emdat-events.nogeom.jsonl.gz")
    assert load.call_args.kwargs["geometry"] is False
    assert not download.call_args.args[2].exists()
    collect.assert_not_called()
    capsys.readouterr()


def test_s3_upload_is_explicit_and_separate_from_legacy_prefix(environment, monkeypatch, capsys):
    args, _, _, _, get_bucket, bucket, _, _ = environment
    monkeypatch.setenv("AWS_BUCKET", "test-bucket")
    assert cli.main([*args, *EXECUTE, "--s3-upload", "--s3-prefix", "test-owner"]) == 0
    saved = json.loads(capsys.readouterr().out)["saved"][0]
    assert saved["s3_key"].startswith("test-owner/media_cloud_articles/snapshots/")
    bucket.upload_file.assert_called_once_with(Path(saved["snapshot"]), saved["s3_key"])
    get_bucket.assert_called_once_with("test-bucket")


def test_missing_s3_prefix_rejected_before_requests(environment, monkeypatch):
    args, load, collect, _, get_bucket, _, _, _ = environment
    monkeypatch.setenv("AWS_BUCKET", "test-bucket")
    with pytest.raises(SystemExit):
        cli.main([*args, *EXECUTE, "--s3-upload"])
    load.assert_not_called()
    collect.assert_not_called()
    get_bucket.assert_not_called()


def test_upload_failure_preserves_local_snapshot(environment, monkeypatch, capsys):
    args, _, _, _, _, bucket, _, _ = environment
    monkeypatch.setenv("AWS_BUCKET", "test-bucket")
    bucket.upload_file.side_effect = OSError("offline test")
    assert cli.main([*args, *EXECUTE, "--s3-upload", "--s3-prefix", "test-owner"]) == 1
    output = json.loads(capsys.readouterr().out)
    assert output["error_type"] == "OSError"
    assert Path(output["local_snapshot"]).is_file()


def test_total_request_budget_stops_before_next_event(environment, capsys):
    args, load, collect, _, _, _, _, pause = environment
    load.side_effect = lambda *_a, **_k: iter([record(), record("event-b")])
    assert cli.main([*args, *EXECUTE, "--max-pages", "10", "--max-body-attempts", "0"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["stop_reason"] == "budget_exhausted"
    assert collect.call_count == 1
    assert collect.call_args.kwargs["max_pages"] == 1
    pause.assert_not_called()


def test_requests_are_spaced_across_events(environment, capsys):
    args, load, collect, _, _, _, _, pause = environment
    load.side_effect = lambda *_a, **_k: iter([record(), record("event-b")])
    assert cli.main([*args, *EXECUTE, "--request-limit", "2", "--max-body-attempts", "0"]) == 0
    assert collect.call_count == 2
    pause.assert_called_once_with(31.0)
    assert json.loads(capsys.readouterr().out)["requests_made"] == 2


def test_selected_model_is_used_once(environment, capsys):
    args, _, _, model, _, _, _, _ = environment
    judge = Mock(return_value=("same_event", "Test excerpt matches.", "Earthquake relief in Japan."))
    model.side_effect = None
    model.return_value = judge
    flags = EXECUTE[:-1]
    assert cli.main([*args, *flags, "--model", "Qwen/Qwen3-1.7B"]) == 0
    model.assert_called_once_with("Qwen/Qwen3-1.7B")
    judge.assert_called_once()
    assert json.loads(capsys.readouterr().out)["saved"][0]["decisions"] == {"same_event": 1, "uncertain": 1}


def test_empty_selection_does_not_load_model_or_collect(environment, capsys):
    args, load, collect, model, *_ = environment
    load.side_effect = lambda *_a, **_k: iter([])
    assert cli.main([*args, *EXECUTE]) == 0
    assert json.loads(capsys.readouterr().out)["stop_reason"] == "no_events"
    collect.assert_not_called()
    model.assert_not_called()


def test_api_error_stops_without_fake_success(environment, capsys):
    args, _, collect, *_ = environment
    collect.side_effect = RuntimeError("offline test")
    assert cli.main([*args, *EXECUTE]) == 1
    output = json.loads(capsys.readouterr().out)
    assert output["stop_reason"] == "error"
    assert output["saved"] == []


def test_missing_api_key_stops_before_collection(environment, monkeypatch):
    args, _, collect, model, *_ = environment
    monkeypatch.delenv("MEDIACLOUD_API_KEY")
    with pytest.raises(SystemExit):
        cli.main([*args, *EXECUTE])
    collect.assert_not_called()
    model.assert_not_called()
