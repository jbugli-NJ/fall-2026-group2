# Montandon missing fields

Snapshot of the staging API on 2026-11-09. USGS is limited to M4.5+.
Per-year counts are in `missing_fields.csv`.

## Largest gaps

Checks that 50% or more of a collection's records fail. Each source is named once; a range spans its events, hazards and impacts collections at or above that level.

- **No `start_datetime`:** cems (100%), charter (100%), glide (100%), usgs (100%)
- **No `end_datetime`:** cems (100%), charter (100%), glide (100%), usgs (100%)
- **A single point:** charter (100%), gdacs (93–100%), glide (100%), idmc-idu (100%), usgs (51–100%), idmc-gidd (93–94%)
- **An extent over 200 km:** gfd (100%), ibtracs (86–98%), ifrcevent (97–98%), emdat (88–89%)
- **Only `UNK` as country:** ibtracs (98–99%), usgs (93%)
- **A blank, `NA` or `None` description:** cems (100%), idmc-gidd (100%), ifrcevent (85%)
- **No `keywords` (older ETL):** cems (100%), ifrcevent (98–99%), usgs (98%), emdat (97%), desinventar (94–96%)
- **No `monty:src_event_id`, the source's own event ID (older ETL):** cems (100%), charter (100%), desinventar (100%), gfd (100%), ifrcevent (99–100%), glide (100%), ibtracs (99–100%), usgs (72–99%), emdat (99%), gdacs (56–65%)
- **No `processing:version` (older, unversioned ETL):** desinventar (100%), gfd (100%), glide (100%), ibtracs (100%), ifrcevent (99–100%), usgs (88–100%), emdat (99%), gdacs (66–73%)
- **No severity value (hazards):** glide (96%), emdat (77%)
- **No impact unit (impacts):** ifrcevent (100%)

## All checks

Each cell is the share of a collection's records that fail the check.
Where that share would round to 0%, the cell gives the number of records instead.

| Check | Meaning |
|---|---|
| `no_datetime` | no `datetime` |
| `no_start_datetime` | no `start_datetime` |
| `no_end_datetime` | no `end_datetime` |
| `end_before_start` | `end_datetime` before `start_datetime` |
| `placeholder_date` | dated before 1800 or after the run date |
| `no_geometry` | no geometry (no `bbox`) |
| `point_only` | a single point |
| `coarse_geometry` | an extent over 200 km |
| `no_country` | no country codes |
| `null_country` | a null country code |
| `unknown_country_only` | only `UNK` as country |
| `no_hazard_codes` | no hazard codes |
| `unrecognised_hazard_code` | a hazard code not in the crosswalk |
| `no_corr_id` | no `monty:corr_id` |
| `blank_description` | a blank, `NA` or `None` description |
| `no_keywords` | no `keywords` (older ETL) |
| `no_src_event_id` | no `monty:src_event_id`, the source's own event ID (older ETL) |
| `no_processing_version` | no `processing:version` (older, unversioned ETL) |
| `no_hazard_detail` | no `monty:hazard_detail` (hazards) |
| `no_severity_value` | no severity value (hazards) |
| `no_impact_detail` | no `monty:impact_detail` (impacts) |
| `no_impact_type` | no impact type (impacts) |
| `no_impact_value` | no impact value (impacts) |
| `no_impact_unit` | no impact unit (impacts) |

| Collection | Records | Failed windows | `no_datetime` | `no_start_datetime` | `no_end_datetime` | `end_before_start` | `placeholder_date` | `no_geometry` | `point_only` | `coarse_geometry` | `no_country` | `null_country` | `unknown_country_only` | `no_hazard_codes` | `unrecognised_hazard_code` | `no_corr_id` | `blank_description` | `no_keywords` | `no_src_event_id` | `no_processing_version` | `no_hazard_detail` | `no_severity_value` | `no_impact_detail` | `no_impact_type` | `no_impact_value` | `no_impact_unit` |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| cems-events | 263 | 0 | 0% | 100% | 100% | 0% | 0% | 0% | 0% | 20% | 0% | 0% | 0% | 0% | 0% | 0% | 0% | 0% | 100% | 0% | 0% | 0% | 0% | 0% | 0% | 0% |
| cems-hazards | 1,259 | 0 | 0% | 100% | 100% | 0% | 0% | 0% | 0% | 1% | 0% | 0% | 0% | 0% | 0% | 0% | 100% | 100% | 100% | 0% | 18% | 18% | 0% | 0% | 0% | 0% |
| cems-impacts | 2,727 | 0 | 0% | 100% | 100% | 0% | 0% | 0% | 0% | 3% | 0% | 0% | 0% | 0% | 0% | 0% | 100% | 100% | 100% | 0% | 0% | 0% | 0% | 0% | 0% | 5% |
| charter-events | 65 | 0 | 0% | 100% | 100% | 0% | 0% | 0% | 100% | 0% | 0% | 0% | 0% | 0% | 0% | 0% | 0% | 0% | 100% | 0% | 0% | 0% | 0% | 0% | 0% | 0% |
| charter-hazards | 456 | 0 | 0% | 100% | 100% | 0% | 0% | 0% | 0% | 6% | 0% | 0% | 0% | 0% | 0% | 0% | 0% | 0% | 100% | 0% | 0% | 0% | 0% | 0% | 0% | 0% |
| desinventar-events | 304,784 | 0 | 0% | 0% | 0% | 0% | 879 records | 0% | 0% | 5% | 0% | 0% | 0% | 0% | 26% | 0% | 41% | 96% | 100% | 100% | 0% | 0% | 0% | 0% | 0% | 0% |
| desinventar-impacts | 344,838 | 0 | 0% | 0% | 0% | 0% | 149 records | 0% | 0% | 4% | 0% | 0% | 0% | 0% | 22% | 0% | 47% | 94% | 100% | 100% | 0% | 0% | 0% | 0% | 0% | 0% |
| emdat-events | 25,398 | 0 | 0% | 0% | 0% | 1% | 0% | 0% | 4% | 89% | 0% | 0% | 0% | 0% | 2% | 0% | 0% | 97% | 99% | 99% | 0% | 0% | 0% | 0% | 0% | 0% |
| emdat-hazards | 25,398 | 0 | 0% | 0% | 0% | 1% | 0% | 0% | 4% | 89% | 0% | 0% | 0% | 0% | 2% | 0% | 0% | 97% | 99% | 99% | 0% | 77% | 0% | 0% | 0% | 0% |
| emdat-impacts | 64,881 | 0 | 0% | 0% | 0% | 1% | 0% | 0% | 5% | 88% | 0% | 0% | 0% | 0% | 2% | 0% | 0% | 97% | 99% | 99% | 0% | 0% | 0% | 0% | 0% | 0% |
| gdacs-events | 61,237 | 0 | 0% | 0% | 0% | 5 records | 0% | 0% | 100% | 0% | 0% | 0% | 1% | 0% | 17% | 0% | 0% | 49% | 56% | 66% | 0% | 0% | 0% | 0% | 0% | 0% |
| gdacs-hazards | 76,649 | 0 | 0% | 0% | 0% | 5 records | 0% | 0% | 33% | 4% | 0% | 0% | 1% | 0% | 17% | 0% | 0% | 46% | 65% | 73% | 0% | 1% | 0% | 0% | 0% | 0% |
| gdacs-impacts | 74,208 | 0 | 0% | 0% | 0% | 5 records | 116 records | 0% | 93% | 3% | 0% | 0% | 3% | 0% | 0% | 0% | 0% | 10% | 13% | 27% | 0% | 0% | 0% | 0% | 0% | 0% |
| gfd-events | 913 | 0 | 0% | 0% | 0% | 0% | 0% | 0% | 0% | 100% | 0% | 0% | 0% | 0% | 0% | 0% | 0% | 0% | 100% | 100% | 0% | 0% | 0% | 0% | 0% | 0% |
| gfd-hazards | 913 | 0 | 0% | 0% | 0% | 0% | 0% | 0% | 0% | 100% | 0% | 0% | 0% | 0% | 0% | 0% | 0% | 0% | 100% | 100% | 0% | 0% | 0% | 0% | 0% | 0% |
| gfd-impacts | 1,826 | 0 | 0% | 0% | 0% | 0% | 0% | 0% | 0% | 100% | 0% | 0% | 0% | 0% | 0% | 0% | 0% | 0% | 100% | 100% | 0% | 0% | 0% | 0% | 0% | 0% |
| glide-events | 8,533 | 0 | 0% | 100% | 100% | 0% | 0% | 0% | 100% | 0% | 0% | 0% | 0% | 0% | 9% | 0% | 3% | 1% | 100% | 100% | 0% | 0% | 0% | 0% | 0% | 0% |
| glide-hazards | 8,533 | 0 | 0% | 100% | 100% | 0% | 0% | 0% | 100% | 0% | 0% | 0% | 0% | 0% | 33 records | 0% | 3% | 1% | 100% | 100% | 0% | 96% | 0% | 0% | 0% | 0% |
| ibtracs-events | 13,496 | 0 | 0% | 0% | 0% | 0% | 0% | 0% | 6 records | 98% | 0% | 0% | 99% | 0% | 0% | 0% | 0% | 0% | 100% | 100% | 0% | 0% | 0% | 0% | 0% | 0% |
| ibtracs-hazards | 724,690 | 0 | 0% | 0% | 0% | 0% | 0% | 0% | 2% | 86% | 2% | 0% | 98% | 0% | 0% | 0% | 0% | 0% | 99% | 100% | 0% | 42% | 0% | 0% | 0% | 0% |
| idmc-gidd-events | 42,393 | 0 | 0% | 0% | 0% | 0% | 0% | 0% | 93% | 3% | 0% | 0% | 0% | 0% | 0% | 0% | 100% | 0% | 0% | 0% | 0% | 0% | 0% | 0% | 0% | 0% |
| idmc-gidd-impacts | 120,047 | 0 | 0% | 0% | 0% | 0% | 0% | 0% | 94% | 2% | 0% | 0% | 0% | 0% | 0% | 0% | 100% | 0% | 0% | 0% | 0% | 0% | 0% | 0% | 0% | 0% |
| idmc-idu-events | 38,374 | 0 | 0% | 0% | 0% | 0% | 0% | 0% | 100% | 0% | 0% | 0% | 0% | 0% | 0% | 0% | 0% | 0% | 0% | 0% | 0% | 0% | 0% | 0% | 0% | 0% |
| idmc-idu-impacts | 116,028 | 0 | 0% | 0% | 0% | 0% | 0% | 0% | 100% | 0% | 0% | 0% | 0% | 0% | 0% | 0% | 0% | 0% | 0% | 0% | 0% | 0% | 0% | 0% | 0% | 0% |
| ifrcevent-events | 2,202 | 0 | 0% | 0% | 0% | 0% | 0% | 0% | 0% | 97% | 0% | 0% | 0% | 0% | 11% | 0% | 85% | 99% | 100% | 100% | 0% | 0% | 0% | 0% | 0% | 0% |
| ifrcevent-impacts | 1,358 | 0 | 0% | 0% | 0% | 0% | 0% | 0% | 0% | 98% | 0% | 0% | 0% | 0% | 14% | 0% | 47% | 98% | 99% | 99% | 0% | 0% | 0% | 0% | 0% | 100% |
| usgs-events | 269,605 | 0 | 0% | 100% | 100% | 0% | 0% | 0% | 100% | 0% | 0% | 43 records | 93% | 0% | 0% | 0% | 0% | 98% | 99% | 100% | 0% | 0% | 0% | 0% | 0% | 0% |
| usgs-hazards | 269,318 | 0 | 0% | 100% | 100% | 0% | 0% | 0% | 96% | 4% | 0% | 43 records | 93% | 0% | 0% | 0% | 0% | 98% | 99% | 100% | 0% | 0% | 0% | 0% | 0% | 0% |
| usgs-impacts | 1,969 | 0 | 0% | 100% | 100% | 0% | 0% | 0% | 51% | 44% | 0% | 0% | 47% | 0% | 0% | 0% | 0% | 42% | 72% | 88% | 0% | 0% | 0% | 0% | 0% | 0% |

No records: `ifrcevent-hazards`, `reference-events`.
