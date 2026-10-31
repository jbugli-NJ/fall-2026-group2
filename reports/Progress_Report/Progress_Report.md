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

## Date: Week 4 - September 29, 2026
- Topics of discussion
  - Moving shared data to S3
    - Saving embeddings and other node data to reuse when building the network
  - Geospatial investigation
    - Using NASA POWER for temperature at disaster locations
    - Limitations with records that don't have point locations
  - NewsAPI collection
    - Improving article relevance using disaster record context
    - Saving results to avoid repeated API requests
  - Initial demo app exploration

- Action Items:

* [x] Build utilities to save node data in S3 and load it into the local network
* [x] Build initial NASA POWER data collection utilities
* [x] Create a news collection pipeline with saved results and API request limits
* [x] Explore Chainlit for the demo app
* [x] Set up the LaTeX report and update the proposal

---

## Date: Week 5 - October 6, 2026
- Topics of discussion
  - Completing first pass at external data connections to the network
    - Temperature data from NASA POWER
    - Querying saved news articles associated with disaster records
  - Demo app framework selection
    - Moving to Streamlit after compatibility issues with Chainlit
    - Showing the queries and source records behind LLM answers
  - Initial LLM benchmarking
    - Testing factual retrieval from the network
    - Comparing Qwen and Gemma models
    - Reliability of tool use across models

- Action Items:

* [x] Connect saved temperature and news data to the network
  * [x] Expose both through tools for LLM queries
* [x] Move news collection storage to S3
* [x] Build an initial Streamlit demo app showing answers and supporting query results
* [x] Create an initial factual retrieval benchmark
  * [x] Run model comparisons and plot results
* [x] Refine LLM tool use based on initial benchmark runs
* [x] Extend the literature review and proposal diagram

---

## Date: Week 6 - October 13, 2026
- Topics of discussion
  - Extending benchmarking to external data
    - Historical weather questions
    - News synthesis questions
    - Comparing answers with and without access to external data
  - News retrieval limitations
    - Improving searches using country and hazard names
    - Further work needed on the news benchmark
  - Preparing the demo app and benchmark results for the Red Cross demo

- Action Items:

* [x] Build weather and news question sets and run initial benchmarks
* [x] Compare model responses with and without weather/news tools
* [x] Refine NewsAPI searches and add random sampling for disaster selection
* [x] Add utilities to refresh Montandon data for further news collection

---

## Date: Week 7 - October 20, 2026
- Topics of discussion
  - API-based LLM integration
    - Using Sonnet 4.5 through AWS Bedrock to query the network
  - Preliminary presentation and updated project direction
    - Extending the demo app toward the proof-of-concept
    - Remaining benchmarking and reporting work
  - Updated news collection using Media Cloud
  - Further benchmark refinement
    - Expanding question sets and comparing answers with and without tools
    - Improving scoring and plots

- Action Items:

* [x] Add Bedrock support for querying the network
* [x] Connect the Bedrock assistant to the demo app
* [x] Prepare preliminary presentation materials
* [x] Update the proposal to reflect the remaining proof-of-concept and evaluation work
* [x] Update benchmarks for a more robust approach
  * [x] Add seeding variation and multiple runs for local LLMs
  * [x] Add hashed inputs to the saved CSV for clarity
* [x] Initial Media Cloud integration and testing over NewsAPI
* [x] Refine benchmark questions, scoring, and plots
* [x] Add pagination for LLM tool queries
* [x] Move the Streamlit app into the demo folder


---

## Date: Week 8 - October 27, 2026 (as of October 30, 2026)
- Topics of discussion
  - News article relevance
    - Using token probabilities to classify Media Cloud articles
  - Initial gap analysis work
    - Determining whether external data fills gaps in Montandon records
    - Comparing answers with and without external data
    - Using LLMs to judge utility by comparing responses

- Action Items:

* [x] Switch article relevance classification to token probabilities
* [x] Build initial gap analysis utilities
  * [x] Add question templates and LLM judging
* [x] Update the proposal to reflect gap analysis work

---
