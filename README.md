
# Exploring Applications for the Montandon Global Crisis Data Bank

## Contributors
- Jeongmin An (zds6799@gmail.com)
- Jehan Bugli (jehan.bugli@gwmail.gwu.edu)
- Aidan Carlisle (aidan.carlisle@gwmail.gwu.edu)

## Overview

This repository explores applications for the International Federal of Red Cross (IFRC)
global crisis data bank, including information on global crisis, impacts, and operational responses.

This aims to create a proof-of-concept pipeline for understanding the available data more intuitively,
blending various approaches through a network science lens.

### Dataset

This project centers around the [Montandon Global Crisis Data Bank](https://montandondata.org/).
The data bank uses a modified version of the [SpatioTemporal Asset Catalogs (STAC) specification](https://stacspec.org/en),
including added custom fields and aggregating information from multiple sources.

Data bank access requires an IFRC GO account and authorization token for API access; contact IFRC for assistance.

## Development

### uv

While standard Python/pip commands can be used, this project is built using [Astral's uv project/package manager](https://docs.astral.sh/uv/).
See linked documentation for installation and basic use.

### Neo4j

This repository contains a [Docker compose](docker-compose.yml) file to set up a local Neo4j instance for testing.
To prep your instance, you can execute the following commands from the repository root (may require alterations based on OS/installation):

```bash
# Launch the Neo4j instance; -d runs it in the background
sudo docker compose up -d

# Set up the new instance with constraints/etc.
uv run -m monty_tool.network.initialize
```

Once running, this should be accessible via [Neo4j browser](http://localhost:7474/browser/).
The test instance is automatically set up with user `neo4j` and password `password`.
The browser UI can be used for exploration and basic queries.

### nbstripout

This project uses `nbstripout` in development dependencies, enforcing it in `.gitattributes` so that notebook outputs are not committed.
Set this up locally with `uv run nbstripout --install` to activate the output filter after syncing.

### Building a local network from S3 data

Tools are available to populate the local Neo4j instance with saved data in an S3 bucket.
Configure the tools before running them:
1. Set `AWS_BUCKET` in the environment to point at the target bucket (likely `dats-capstone`)
2. Set `AWS_BUCKET_PREFIX` to your S3 folder prefix, such as `my.name@example.com/` or `team/weather-test/`
3. Make sure AWS credentials are available to the CLI and boto3

S3 folders are object key prefixes. Surrounding whitespace and slashes are trimmed,
and one trailing slash is added; nested paths are supported. If `AWS_BUCKET_PREFIX`
is unset, blank, or only slashes, the tools log a warning and fall back to
`aidan.carlisle@gwu.edu/`. Set it before launching a tool because paths are resolved
when the shared resources module loads. `AWS_BUCKET` still defaults to `dats-capstone`.

`update_network_node_data` reads the source files under `<prefix>/raw/` and `<prefix>/go/`. It validates the records, generates Montandon embeddings, and writes the resulting node data under `<prefix>/node_data/`.

```bash
export AWS_BUCKET="my-bucket"
AWS_BUCKET_PREFIX="team/weather-test" uv run update-network-node-data
```

`setup_network` downloads that node data and rebuilds the local Neo4j graph.
Use carefully because this clears the existing local graph before loading new data.
This must be run after the initial Docker compose step to set up the Neo4j instance.

```bash
AWS_BUCKET_PREFIX="team/weather-test" uv run setup-network
```

Use the same bucket and prefix when generating and loading node data. For example,
set `export AWS_BUCKET_PREFIX="team/weather-test"` before running both commands.

### Updating NASA POWER data in S3

The NASA POWER tool uses the same bucket and prefix settings. It reads Montandon
records from `<prefix>/raw/` and writes weather results to
`<prefix>/nasa_power/weather.jsonl.gz`.

```bash
export AWS_BUCKET="my-bucket"
export AWS_BUCKET_PREFIX="my.name@example.com/"
uv run update-nasa-power-data
```
