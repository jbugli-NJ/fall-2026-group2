"""Load classified Media Cloud news into an existing local graph."""

from neo4j import Driver

from monty_tool.boto3_utils.s3_protocols import S3Bucket
from monty_tool.news.s3_storage import iter_media_cloud_snapshots
from monty_tool.network.insert import insert_media_cloud_articles
from monty_tool.network.node_data import media_cloud_run_to_node_data
from monty_tool.tools.resources import BUCKET_DATA_PREFIX


def load_news_into_graph(
    bucket: S3Bucket, *, bucket_name: str, driver: Driver
) -> int:
    """Reconcile each current snapshot with its existing Event node."""
    processed = 0

    for key, run in iter_media_cloud_snapshots(
        bucket, prefix=BUCKET_DATA_PREFIX
    ):
        rows = media_cloud_run_to_node_data(
            run,
            snapshot_s3_uri=f"s3://{bucket_name}/{key}",
        )
        # Pass even an empty result, so a refresh can retract every old link.
        # All batches for this event must share the deletion's transaction.
        processed += insert_media_cloud_articles(
            driver, rows, event_id=run.job.event.item_id,
            snapshot_s3_uri=f"s3://{bucket_name}/{key}",
        )

    return processed
