"""Collect Media Cloud candidates, assess evidence, and save snapshots."""

import argparse
import json
import os
import sys
from collections import Counter
from collections.abc import Sequence
from contextlib import nullcontext
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from time import sleep

from dotenv import load_dotenv

from monty_tool.boto3_utils.s3_protocols import S3Bucket
from monty_tool.boto3_utils.s3_utils import download_object, get_bucket
from monty_tool.data_cache import load_collection, raw_cache_path
from monty_tool.event_context import EventContext
from monty_tool.news.pipeline import prepare_media_cloud_jobs, select_disaster_records
from monty_tool.news.runner import run_media_cloud_job
from monty_tool.news.schemas import MediaCloudStory
from monty_tool.news.s3_storage import save_media_cloud_snapshot, upload_media_cloud_snapshot
from monty_tool.news.verification import EventJudge, JudgeResult, make_qwen_event_judge
from monty_tool.utils.versions import FrozenModel


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("Must be a positive integer.")
    return number


def non_negative_int(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("Must be a non-negative integer.")
    return number


def deferred_judge(
    event: EventContext, story: MediaCloudStory, body_text: str,
) -> JudgeResult:
    return "uncertain", "Verification explicitly deferred; no model judgment performed.", None


def load_qwen_judge(model: str) -> EventJudge:
    # Dry runs and deferred runs must not import or load the model.
    from monty_tool.llm.client import QueryAssistant

    assistant = QueryAssistant(model_id=FrozenModel(model), enable_external=False)
    return make_qwen_event_judge(assistant)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Show searches without news requests or writes.")
    mode.add_argument("--execute", action="store_true", help="Save local snapshots; S3 upload is opt-in.")
    parser.add_argument("--collection", required=True, help="Montandon collection, e.g. emdat-events.")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--cache-dir", type=Path, help="Read existing local raw data instead of S3.")
    source.add_argument("--s3-source-key", help="Explicit raw-data S3 key; requires AWS_BUCKET.")
    parser.add_argument("--no-geometry", action="store_true")
    parser.add_argument("--start-date", type=date.fromisoformat, required=True)
    parser.add_argument("--end-date", type=date.fromisoformat, required=True)
    parser.add_argument("--event-id", action="append", help="Select exact raw record ID; repeat for more events.")
    parser.add_argument("--random", action="store_true")
    parser.add_argument("--max-records", type=positive_int)
    parser.add_argument("--days-before", type=non_negative_int, default=1)
    parser.add_argument("--days-after", type=non_negative_int, default=3)
    parser.add_argument("--media-collection-id", type=positive_int, action="append", required=True,
                        help="Media Cloud source collection ID; repeat to include more collections.")
    parser.add_argument("--page-size", type=positive_int, default=100)
    parser.add_argument("--max-pages", type=positive_int, default=1, help="Maximum search pages per event.")
    parser.add_argument("--request-limit", type=positive_int,
                        help="Maximum Media Cloud search requests across this run; required for execution.")
    parser.add_argument("--max-body-attempts", type=non_negative_int,
                        help="Maximum publisher fetch attempts per event; required for execution.")
    judge = parser.add_mutually_exclusive_group()
    judge.add_argument("--model", choices=[
        FrozenModel.QWEN3_1_7B.value, FrozenModel.QWEN3_5_4B.value, FrozenModel.QWEN3_5_9B.value,
    ])
    judge.add_argument("--defer-verification", action="store_true",
                       help="Preserve evidence as uncertain without loading Qwen.")
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/media-cloud"))
    parser.add_argument("--s3-upload", action="store_true", help="Also upload each saved snapshot to S3.")
    parser.add_argument("--s3-prefix", help="Upload folder; otherwise uses explicit AWS_BUCKET_PREFIX.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.start_date > args.end_date:
        parser.error("--start-date must not be later than --end-date.")
    if args.page_size > 100:
        parser.error("--page-size must be between 1 and 100.")
    if args.execute and (args.request_limit is None or args.max_body_attempts is None):
        parser.error("--execute requires --request-limit and --max-body-attempts.")
    if args.execute and args.model is None and not args.defer_verification:
        parser.error("--execute requires --model or --defer-verification.")
    if args.s3_upload and not args.execute:
        parser.error("--s3-upload requires --execute.")
    if args.s3_prefix is not None and not args.s3_upload:
        parser.error("--s3-prefix requires --s3-upload.")
    if not args.collection.strip() or Path(args.collection).name != args.collection or args.collection in {".", ".."}:
        parser.error("--collection must be a collection name, not a path.")

    bucket_name = os.getenv("AWS_BUCKET", "").strip()
    raw_prefix = os.getenv("AWS_BUCKET_PREFIX", "").strip().strip("/")
    upload_prefix = (args.s3_prefix if args.s3_prefix is not None else raw_prefix).strip().strip("/")
    needs_s3 = args.cache_dir is None or args.s3_upload
    if needs_s3 and not bucket_name:
        parser.error("Set AWS_BUCKET explicitly for S3 access.")
    if args.cache_dir is None and args.s3_source_key is None and not raw_prefix:
        parser.error("Set AWS_BUCKET_PREFIX or provide --s3-source-key for S3 input.")
    if args.s3_upload and not upload_prefix:
        parser.error("S3 upload requires --s3-prefix or explicit AWS_BUCKET_PREFIX.")

    bucket: S3Bucket | None = get_bucket(bucket_name) if needs_s3 else None
    source_context = (
        nullcontext(str(args.cache_dir)) if args.cache_dir is not None
        else TemporaryDirectory()
    )
    with source_context as source_directory:
        cache_dir = Path(source_directory)
        if args.cache_dir is None:
            assert bucket is not None
            suffix = ".nogeom.jsonl.gz" if args.no_geometry else ".jsonl.gz"
            key = args.s3_source_key or f"{raw_prefix}/raw/{args.collection}{suffix}"
            download_object(bucket, key, raw_cache_path(
                args.collection, cache_dir=cache_dir, geometry=not args.no_geometry,
            ))
        records = load_collection(args.collection, cache_dir=cache_dir, geometry=not args.no_geometry)
        if args.event_id:
            requested = set(args.event_id)
            matching = [record for record in records if record.get("id") in requested]
            missing = requested - {record.get("id") for record in matching}
            if missing:
                parser.error(f"Event IDs not found: {sorted(missing)}")
            records = iter(matching)
        events = select_disaster_records(
            records, start_date=args.start_date, end_date=args.end_date,
            max_records=args.max_records, randomize=args.random,
        )
        jobs = prepare_media_cloud_jobs(
            events, collection_ids=args.media_collection_id,
            days_before=args.days_before, days_after=args.days_after,
        )

    if args.dry_run:
        print(json.dumps({
            "mode": "dry_run", "provider": "media_cloud", "planned_jobs": len(jobs),
            "media_cloud_requests_made": 0, "page_size": args.page_size,
            "max_pages_per_event": args.max_pages, "request_limit": args.request_limit,
            "output_dir": str(args.output_dir),
            "jobs": [job.model_dump(mode="json") for job in jobs],
        }, ensure_ascii=False, indent=2))
        return 0

    if not jobs:
        print(json.dumps({"stop_reason": "no_events", "saved": [], "requests_made": 0}))
        return 0
    if not os.getenv("MEDIACLOUD_API_KEY", "").strip():
        parser.error("MEDIACLOUD_API_KEY is required for execution.")

    if args.defer_verification:
        judge = deferred_judge
    else:
        assert args.model is not None
        judge = load_qwen_judge(args.model)
    saved: list[dict[str, object]] = []
    requests_made = 0
    stop_reason = "completed"
    for job in jobs:
        remaining = args.request_limit - requests_made
        if remaining <= 0:
            stop_reason = "budget_exhausted"
            break
        if requests_made:
            sleep(31.0)  # Also space requests across different events.
        snapshot: Path | None = None
        try:
            result = run_media_cloud_job(
                job, judge=judge, max_body_attempts=args.max_body_attempts,
                page_size=args.page_size, max_pages=min(args.max_pages, remaining),
            )
            requests_made += result.pages_fetched
            snapshot = save_media_cloud_snapshot(result, output_dir=args.output_dir)
            entry: dict[str, object] = {
                "event_id": job.event.item_id, "snapshot": str(snapshot),
                "candidates": len(result.assessments),
                "decisions": dict(Counter(item.decision.status for item in result.assessments)),
                "has_more_pages": bool(result.next_page_token),
                "verification_mode": "deferred" if args.defer_verification else "qwen",
            }
            saved.append(entry)
            print(f"Saved: {snapshot}", file=sys.stderr)
            if args.s3_upload:
                assert bucket is not None
                entry["s3_key"] = upload_media_cloud_snapshot(bucket, snapshot, prefix=upload_prefix)
        except Exception as exc:
            print(json.dumps({
                "stop_reason": "error", "event_id": job.event.item_id,
                "error_type": type(exc).__name__, "saved": saved,
                "local_snapshot": str(snapshot) if snapshot is not None else None,
            }, ensure_ascii=False, indent=2))
            return 1
    print(json.dumps({"stop_reason": stop_reason, "requests_made": requests_made, "saved": saved},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
