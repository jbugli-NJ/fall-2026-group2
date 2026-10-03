"""Load saved NewsAPI results into an existing local graph."""

from itertools import batched

from neo4j import Driver

from monty_tool.boto3_utils.s3_protocols import S3Bucket
from monty_tool.news.s3_storage import iter_news_snapshots
from monty_tool.network.insert import insert_news_articles
from monty_tool.network.node_data import news_result_to_node_data
from monty_tool.network.resources import NETWORK_INSERT_BATCH_SIZE


def load_news_into_graph(
    bucket: S3Bucket, *, bucket_name: str, driver: Driver
) -> int:
    """Insert saved news candidates without rebuilding the graph."""
    processed = 0

    for key, job, result in iter_news_snapshots(bucket):
        rows = news_result_to_node_data(
            job,
            result,
            snapshot_s3_uri=f"s3://{bucket_name}/{key}",
        )
        for batch in batched(rows, NETWORK_INSERT_BATCH_SIZE):
            processed += insert_news_articles(driver, list(batch))

    return processed
