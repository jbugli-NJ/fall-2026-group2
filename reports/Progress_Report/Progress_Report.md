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

## Date: Week 8 - October 27, 2026 (as of November 2, 2026)
- Topics of discussion
  - News article relevance
    - Using token probabilities to classify Media Cloud articles
  - Initial gap analysis work
    - Determining whether external data fills gaps in Montandon records
    - Comparing answers with and without external data
    - Using LLMs to judge utility by comparing responses
  - Geospatial data
    - Collecting hazard footprints from CEMS, GDACS, and USGS ShakeMap
    - Checking how much location detail each source provides

- Action Items:

* [x] Switch article relevance classification to token probabilities
* [x] Build initial gap analysis utilities
  * [x] Add question templates and LLM judging
* [x] Update the proposal to reflect gap analysis work
* [x] Build initial hazard footprint collection tools

---

## Date: Week 9 - November 3, 2026
- Topics of discussion
  - First gap analysis runs
    - Comparing the contribution from news, weather, and footprints separately
    - Adjusting questions to focus on Red Cross Early Action Protocols (EAPs)
    - Using past disasters for a country and hazard to support planning
  - Connecting hazard footprints to the network
  - Report updates
    - Documenting data sources, coverage, and how they connect

- Action Items:

* [x] Connect hazard footprints to the network and LLM tools
* [x] Add separate toggles for news, weather, and external footprints
* [x] Update gap analysis questions for EAP planning
* [x] Run initial evaluations and save outputs for review
* [x] Move shared data to the team's S3 folder
* [x] Add the data sources section to the report

---

## Date: Week 10 - November 10, 2026
- Topics of discussion
  - Checking the LLM judge's answers
    - Building a way to label responses by hand and compare judgments
    - Further changes needed based on the initial evaluation outputs
  - Missing fields in Montandon
    - Comparing gaps by source and year
    - Missing processing fields, unknown countries, and limited location detail
  - Initial research paper draft

- Action Items:

* [x] Build a terminal tool for hand-labeling gap analysis responses
  * [x] Save initial labels and compare with the LLM judge
* [x] Complete the missing-field analysis and coverage plot
  * [x] Summarize the largest gaps for the report
* [x] Refine gap analysis based on the initial results
* [x] Start the research paper draft

---

## Date: Week 11 - November 17, 2026 (in progress)
- Topics of discussion
  - Reworking gap analysis after reviewing earlier runs
    - Providing the model with retrieved context directly rather than relying on tool calls
    - Revising prompts to avoid misinterpretations
    - Reducing the judging categories to three main options
    - Rerunning evaluations and labeling the new responses
  - Estimating Bedrock costs for the evaluations
    - Recording judge token usage and adding a cost calculation script
  - Continuing the research paper

- Action Items:

* [x] Finalize the gap analysis changes and new evaluation runs
* [x] Complete another hand-labeling pass
* [x] Finish the Bedrock cost calculation script
* [x] Extend the existing research paper draft

---
