"""
Command-line entry point for news collection from S3 disaster records.
"""

import argparse
import json
from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path

from monty_tool.data_cache import load_collection
from monty_tool.news.pipeline import (
    prepare_news_jobs,
    select_disaster_records,
)
from monty_tool.news.runner import run_news_collection
from tempfile import TemporaryDirectory

from monty_tool.boto3_utils.s3_utils import get_bucket
from monty_tool.news.s3_storage import (
    download_collection_cache,
    upload_news_snapshot,
)
from monty_tool.tools.resources import get_env_bucket_name, get_env_bucket_prefix

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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect news for a selected subset of disaster records."
    )

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Show planned searches without calling NewsAPI or saving results.",
    )
    mode.add_argument(
        "--execute",
        action="store_true",
        help="Run collection, save local snapshots, and upload results to S3.",
    )

    parser.add_argument("--collection", required=True)
    parser.add_argument(
        "--s3-source-key",
        help="Override the default S3 key: AWS_BUCKET_PREFIX/raw/<collection>.jsonl.gz.",
    )
    parser.add_argument(
        "--no-geometry",
        action="store_true",
        help="Download the collection's .nogeom.jsonl.gz S3 object.",
    )
    parser.add_argument("--start-date", type=date.fromisoformat, required=True)
    parser.add_argument("--end-date", type=date.fromisoformat, required=True)
    parser.add_argument(
        "--random",
        action="store_true",
        help="Shuffle eligible disaster events before applying the record limit.",
    )
    parser.add_argument(
        "--max-records",
        type=positive_int,
        help="Limit selected disaster events; defaults to all events in the date range.",
    )
    parser.add_argument("--days-before", type=non_negative_int, default=1)
    parser.add_argument("--days-after", type=non_negative_int, default=3)
    parser.add_argument("--page-size", type=positive_int, default=100)
    parser.add_argument("--refresh-hours", type=positive_int, default=24)
    parser.add_argument(
        "--request-limit",
        type=positive_int,
        help="Maximum NewsAPI requests per run; required with --execute.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("outputs/news-collection"),
    )

    return parser

def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.start_date > args.end_date:
        parser.error("--start-date must not be later than --end-date.")

    if args.page_size > 100:
        parser.error("--page-size must be between 1 and 100.")

    if args.execute and args.request_limit is None:
        parser.error("--execute requires --request-limit.")

    source_key = args.s3_source_key
    if source_key is None:
        suffix = ".nogeom.jsonl.gz" if args.no_geometry else ".jsonl.gz"
        source_key = f"{get_env_bucket_prefix()}raw/{args.collection}{suffix}"

    bucket = get_bucket(get_env_bucket_name())
    with TemporaryDirectory() as source_directory:
        cache_dir = Path(source_directory)

        download_collection_cache(
            bucket,
            source_key=source_key,
            collection=args.collection,
            cache_dir=cache_dir,
            geometry=not args.no_geometry,
        )

        records = load_collection(
            args.collection,
            cache_dir=cache_dir,
            geometry=not args.no_geometry,
        )
        events = select_disaster_records(
            records,
            start_date=args.start_date,
            end_date=args.end_date,
            max_records=args.max_records,
            randomize=args.random,
        )
        jobs = prepare_news_jobs(
            events,
            days_before=args.days_before,
            days_after=args.days_after,
        )

    if args.dry_run:
        plan = {
            "mode": "dry_run",
            "planned_jobs": len(jobs),
            "news_api_requests_made": 0,
            "page_size": args.page_size,
            "refresh_hours": args.refresh_hours,
            "output_dir": str(args.output_dir),
            "jobs": [
                {
                    "collection": job.event.collection,
                    **job.query.model_dump(mode="json"),
                }
                for job in jobs
            ],
        }
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0

    summary = run_news_collection(
        jobs,
        output_dir=args.output_dir,
        request_limit=args.request_limit,
        page_size=args.page_size,
        refresh_after=timedelta(hours=args.refresh_hours),
        s3_bucket=bucket,
    )
    for snapshot in [*summary.collected, *summary.reused]:
        upload_news_snapshot(bucket, snapshot)
    print(summary.model_dump_json(indent=2))

    return 1 if summary.stop_reason == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
