## Date: Week 1 - September 8, 2026
- Topics of discussion
    - Group work split, general direction
  - Project selection: Montandon Data Bank vs. End of Mission (EOM) reports
  - Codebase setup, including GitHub Actions PR validation and general collaboration

- Action Items:

* [x] Create initial proposal
  * [x] Sketch out rough responsibility split
* [x] Make a project determination: Montandon vs. EOM
* [x] Set up GitHub repository
  * [x] Create initial type checking and unit testing requirements for merges

---

## Date: Week 2 - September 15, 2026
- Topics of discussion
  - Updated proposal
    - Work split between group members
    - Steps past initial network (e.g. LLM integration)
  - Reviewing EDA and updating schema in response to validation failures with historical data

- Action Items:

* [x] Create a more detailed proposal
  * [x] Include a Draw.io diagram
* [x] Provide more detail on network measures
* [x] Look into potential NewsAPI / LLM integrations to do more on response enablement
* [x] Create a clearer work split between the 3 members
* [x] Determine external geospatial integrations (e.g. temperature at location)
* [x] Complete EDA work on the full dataset, downloading it
* [x] Create initial embedding utilities

---

## Date: Week 3 - September 22, 2026
- Topics of discussion
  - Initial end-to-end setup for the LLM to query the network
    - Nodes and edges used in the network
    - HuggingFace chat template setup
    - Tools to expose for Cypher queries
  - Geospatial investigation
    - Challenges with reconciling different geospatial data sources
  - NewsAPI exporation
    - Reranking article sources to surface more relevant items

- Action Items:

* [x] Build utilities for inserting data into networks
  * [x] Montandon API
  * [x] GO API events
  * [x] GO API appeals
* [x] Wire up initial full LLM tests to respond to queries with NewsAPI/Cypher queries
* [x] Explore geospatial data options and potential integrationsm
* [x] Refine NewsAPI tooling and article reranking

---
