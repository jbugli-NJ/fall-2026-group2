
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

### System dependencies

While standard Python/pip commands can be used, this project is built using [Astral's uv project/package manager](https://docs.astral.sh/uv/).
See linked documentation for installation and basic use.

Other tools include:
- [The AWS CLI](https://aws.amazon.com/cli/) to authenticate S3 bucket access
- `python3.14-dev` (may be required for Ubuntu as a PyTorch dependency)

### Bedrock authentication

AWS SSO credentials can be used to access Bedrock automatically.
Sonnet 4.5 (`us.anthropic.claude-sonnet-4-5-20250929-v1:0`) is the whitelisted model ID.

### Benchmarking

```bash
uv run run-fact-benchmark --input-json 20261008_historical_weather.json
```

A benchmarking pipeline is available to test models against stored questions using network data.
By default, each benchmark invocation evaluates all stored benchmark seeds.
Each seed runs the selected questions twice, enabling and disabling query tools.
`--model` selects the model and `--limit` restricts both runs to the first N questions.
Use `--seed` to evaluate just one seed, for example:

```bash
uv run run-fact-benchmark --seed 2028 --input-json 20261008_news_synthesis.json
```

Use `--model bedrock` to evaluate the Bedrock assistant's default Sonnet 4.5 model with AWS credentials instead of a local model.
Reports and summaries record the Bedrock model ID; its revision is recorded as `not applicable`.

Results are saved under `benchmarks/<input name>/`.
Each seed run writes separate Markdown reports under `reports/`, named `<model>_<timestamp>.md`
and `<model>_no_tools_<timestamp>.md`.
CSV summaries and plots stay in the input's results directory as `summary.csv` and `summary.svg`
and record every seed run.
The baseline is labeled `(no tools)` in reports, summaries, and plots.

Markdown reports and CSV summaries store the full SHA-256 hash of the input file's
raw bytes, including whitespace. The hash covers the entire file even when runs are limited.

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

### Demo app

The Streamlit demo in [`demo/`](demo/) answers questions with the assistant and shows
each graph query it ran, with its arguments and returned rows, and the record IDs
behind the answer. Run it from the repository root so it picks up
`.streamlit/config.toml`:

```bash
# Bedrock (Sonnet 4.5) against the local Neo4j graph
uv run streamlit run demo/app.py

# The local Qwen3.5-4B model instead of Bedrock
MONTY_UI_LOCAL=1 uv run streamlit run demo/app.py

# Replay the saved example in demo/fixtures.json; no graph or model needed
MONTY_UI_REPLAY=1 uv run streamlit run demo/app.py
```

When Neo4j is not reachable on `localhost:7687`, the app falls back to the replay.

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

### Updating raw Montandon data

`update-raw-data` fetches complete collections into `data/raw/` and uploads them
to `<AWS_BUCKET_PREFIX>/raw/` in `AWS_BUCKET`. It uses `MONTANDON_API_TOKEN`.
Each run replaces the selected collection files with a fresh download.

```bash
uv run update-raw-data --collection emdat-events --no-geometry
```

Omitting `--collection` refreshes all Montandon collections. The flag accepts
multiple collection IDs. `--no-geometry` produces `.nogeom.jsonl.gz` files;
`--no-upload` keeps the downloaded files locally.

### Collecting Media Cloud news from S3 disaster records

The news collector reads `<AWS_BUCKET_PREFIX>/raw/<collection>.jsonl.gz` from `AWS_BUCKET`.
Set those variables and AWS credentials as described above.
For execution, obtain a personal [Media Cloud API key](https://www.mediacloud.org/documentation/search-api-guide)
and set `MEDIACLOUD_API_KEY` locally.

Find source collection IDs in the [Media Cloud Directory](https://www.mediacloud.org/media-cloud-directory).
For example, `34412234` selects US National news sources. A collection selects
publishers, not event locations. The same selected collections are used for
every event in a run; repeat `--media-collection-id` for broader coverage.

This example previews planned searches for GDACS events in the date range, using US National news sources. It is an example of the CLI workflow, not a global news collection.

```bash
uv run python -m monty_tool.news.cli --dry-run \
  --collection gdacs-events \
  --start-date 2025-01-01 --end-date 2025-06-01 \
  --media-collection-id 34412234
```
`--dry-run` reads the disaster data and shows planned searches without making
Media Cloud requests or saving results. To collect and classify, use `--execute`
instead and supply a Qwen `--model`, a run-wide `--request-limit`, and a
per-event `--max-body-attempts`. Add `--s3-upload` to save results to S3;
otherwise results are saved locally. Use `--refresh` to recollect and replace
an event's current snapshot.

Choose collection IDs that cover the events of interest: the US collection
alone does not provide global publisher coverage. If the raw input is a
`.nogeom.jsonl.gz` file, add `--no-geometry`. Publisher bodies may be
unavailable, leaving some candidates `uncertain`.

### Updating NASA POWER data in S3

The NASA POWER tool uses the same bucket and prefix settings. It reads Montandon
records from `<prefix>/raw/` and writes weather results to
`<prefix>/nasa_power/weather.jsonl.gz`.

```bash
export AWS_BUCKET="my-bucket"
export AWS_BUCKET_PREFIX="my.name@example.com/"
uv run update-nasa-power-data
```
