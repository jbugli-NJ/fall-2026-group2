"""Add saved S3 news results to an existing local Neo4j graph."""

import os

from monty_tool.boto3_utils.s3_utils import get_bucket
from monty_tool.network.initialize import initialize_db
from monty_tool.network.news import load_news_into_graph
from monty_tool.network.resources import get_graph_db_driver
from monty_tool.tools.resources import get_env_bucket_name


def main() -> int:
    if not os.getenv("AWS_BUCKET_PREFIX", "").strip():
        raise ValueError("Set AWS_BUCKET_PREFIX to your own S3 folder.")

    bucket_name = get_env_bucket_name()
    bucket = get_bucket(bucket_name)

    initialize_db()
    with get_graph_db_driver() as driver:
        processed = load_news_into_graph(
            bucket, bucket_name=bucket_name, driver=driver
        )

    print(f"Processed {processed} NewsAPI article-event links.")
    return processed

if __name__ == "__main__":
    main()