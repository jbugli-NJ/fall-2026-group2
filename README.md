
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
