"""
Refresh raw Montandon collections and upload them to S3.
"""

import argparse
import logging
from collections.abc import Sequence

from dotenv import load_dotenv

from monty_tool.api_utils import get_pystac_client
from monty_tool.boto3_utils.s3_utils import get_bucket, upload_object
from monty_tool.data_cache import pull_collection
from monty_tool.tools.resources import get_env_bucket_name, get_env_bucket_prefix

logger = logging.getLogger(__name__)


def main(argv: Sequence[str] | None = None) -> int:
    """
    Download selected collections, or all collections, and upload raw files.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--collection", nargs="+", default=None,
        help="Collection IDs to refresh; defaults to all Montandon collections.",
    )
    parser.add_argument(
        "--no-geometry", action="store_true",
        help="Fetch records with geometry excluded and save .nogeom.jsonl.gz files.",
    )
    parser.add_argument(
        "--no-upload", action="store_true",
        help="Keep downloaded files in data/raw locally.",
    )
    args = parser.parse_args(argv)
    load_dotenv()
    logging.basicConfig(level=logging.INFO)

    client = get_pystac_client()
    collections = args.collection
    if collections is None:
        collections = [collection.id for collection in client.get_collections()]

    bucket_name = None if args.no_upload else get_env_bucket_name()
    bucket = get_bucket(bucket_name) if bucket_name is not None else None

    for collection_id in collections:
        logger.info("Pulling raw records for %s.", collection_id)
        path = pull_collection(collection_id, client=client, geometry=not args.no_geometry)
        logger.info("Saved %s.", path)
        if bucket is not None:
            key = f"{get_env_bucket_prefix()}raw/{path.name}"
            upload_object(bucket, path, key)
            logger.info("Uploaded s3://%s/%s.", bucket_name, key)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
