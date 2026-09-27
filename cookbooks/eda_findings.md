# EDA findings — Montandon staging API

Snapshot taken 2026-09-12 against `montandon-eoapi-stage.ifrc.org`. All numbers come from
[`eda_nb.ipynb`](eda_nb.ipynb); raw pulls are cached under `data/raw/` (gitignored) and can be
regenerated with the notebook's pull cell.

**Caveat on all counts:** the staging database is being loaded live. `gdacs-events` grew from
43,595 to 43,723 items over ~4 hours on the day of the census. Treat every number as a snapshot.

## TL;DR

1. **The API schema (`api_schemas.py`) rejects ~97% of real items.** `monty:src_event_id` and
   `keywords` are required but present on only 1–31% of items. This is not a bug in the schema —
   it matches what the ETL produces *today* — but ~97% of the bank is backfilled history that
   predates those fields.
2. **The missing fields are missing by ETL generation, not by collection.** Items dated 2025+
   have `keywords`; items dated 2026 also have `monty:src_event_id` and `processing:version 0.2.4`.
   Same pattern in every collection. Whether IFRC will reprocess history is a stakeholder question.
3. **`description` is mostly templated or placeholder text.** EM-DAT and GDACS are formulaic
   one-liners; 85% of IFRC event descriptions are the literal string `NA`, and the rest is raw HTML.
4. **`monty:corr_id` links events to impacts within a source (100%) but almost never across
   sources (0.8%), and by construction never will:** GDACS puts its own event ID where EM-DAT
   puts `1`. IFRC's own cookbook links across sources by date + bbox + hazard code instead.
   The ETL repo gives us the inputs to do the same.
5. **`monty:hazard_codes` mixes at least three vocabularies** in one field (EM-DAT, UNDRR-ISC,
   GLIDE). The ETL repo ships the crosswalk (`HazardProfiles.csv`, 303 rows) that normalises them.
6. **`keywords` is derived from `hazard_codes` + `country_codes` by the ETL**, not source text.
   Reconstructible for the 97% missing it; carries no extra signal for embeddings.
7. **EM-DAT geometry is full-resolution country polygons, up to 10 MB per item**, and is >99% of
   the payload. It duplicates `monty:country_codes`.

## What was pulled

| Collection | Items | Geometry | Why |
|---|---|---|---|
| `emdat-events` | 25,308 | excluded | Canonical disaster record, 1900–present |
| `emdat-impacts` | 64,637 | excluded | Deaths / affected / cost per event |
| `gdacs-events` | 43,723 | Point | Second source with alert levels; overlap test vs EM-DAT |
| `ifrcevent-events` | 2,199 | Polygon | Only collection with IFRC operational data |
| `ifrcevent-impacts` | 1,349 | Polygon | " |

137,216 items, 48 MB gzipped. Skipped: IBTrACS hazards (722,837 items — one per storm track
point), PDC (165k+), USGS (>1M), DesInventar (uncounted), empty collections (`cems-*`, `reference-events`,
`ifrcevent-hazards`).

## 1. Field coverage

% of items with a non-null value. Fields *not* in `api_schemas.py` are marked †; the schema's
`extra="ignore"` drops them silently.

| Field | emdat-events | emdat-impacts | gdacs-events | ifrcevent-events | ifrcevent-impacts |
|---|---|---|---|---|---|
| `title`, `description`, `datetime`, `start/end_datetime`, `roles` | 100 | 100 | 100 | 100 | 100 |
| `monty:corr_id`, `monty:country_codes`, `monty:hazard_codes`, `monty:episode_number` | 100 | 100 | 100 | 100 | 100 |
| `monty:etl_id` † | 100 | 100 | 100 | 100 | 100 |
| `keywords` | 3 | 3 | 31 | 1 | 2 |
| `monty:src_event_id` | 1 | 1 | 21 | 0 | 0 |
| `processing:software`, `processing:version` † | 1 | 1 | 3 | 0 | 0 |
| `monty:guid` † | 0 | 0 | 41 | 0 | 0 |
| `severitydata.*` | 0 | 0 | 31 | 0 | 0 |
| `monty:impact_detail.{type,category,value,estimate_type}` | – | 100 | – | – | 100 |
| `monty:impact_detail.unit` | – | 100 | – | – | **0** |
| `assets` | 0 | 0 | 100 | 0 | 0 |

Notes:
- `severitydata` is a GDACS field; the schema puts it on *impact* properties, but here it shows up
  on 31% of GDACS *events*.
- `monty:impact_detail.unit` is on 100% of EM-DAT impacts and 0% of IFRC impacts. The schema
  requires it.

## 2. ETL generations

Field presence by item year, EM-DAT events (other collections follow the same pattern):

| Year | Items | `keywords` | `monty:src_event_id` | `processing:version` |
|---|---|---|---|---|
| ≤ 2024 | 24,618 | 0% | 0% | 0% |
| 2025 | 449 | 92% | 0% | 0% |
| 2026 | 241 | 100% | 100% | 100% (`0.2.4`) |

GDACS is the same shape with more 2026 volume (11,265 items, 100% / 82% / 12%).

The one `processing:version` value seen anywhere is `0.2.4` (`pystac-monty`). Items without it
were produced by an earlier, unversioned ETL.

**Open question for IFRC:** will historical records be reprocessed with the current ETL? If yes,
the schema is right and we wait. If no, ~97% of the bank permanently lacks `keywords` and
`src_event_id`, and the schema needs them optional.

## 3. Schema validation on real data

`MontandonItem.model_validate` on every cached item (EM-DAT given a bbox-centre Point in place
of its excluded geometry):

| Collection | Items | Valid | Invalid |
|---|---|---|---|
| emdat-events | 25,308 | 241 | 99.0% |
| emdat-impacts | 64,637 | 661 | 99.0% |
| gdacs-events | 43,723 | 9,307 | 78.7% |
| ifrcevent-events | 2,199 | 4 | 99.8% |
| ifrcevent-impacts | 1,349 | 4 | 99.7% |

Root-cause fields (on the matching property model; Pydantic also reports the other two union
branches failing, which is noise):

| Missing field | emdat | gdacs | ifrc |
|---|---|---|---|
| `monty:src_event_id` | 99% | 79% | 99.8% |
| `keywords` | 97% | 69% | 98–99% |
| `monty:impact_detail.unit` | – | – | 99.7% (impacts) |

The `embedding-setup` branch tightens `title`/`description` to non-blank, which is fine (0 blanks
found), and calls `properties.keywords` unconditionally in `extract_field_texts`, which will raise
on ~97% of real items once validation passes.

Minimal schema change that would validate the whole cache:

```python
keywords: list[str] = []
monty_src_event_id: str | None = Field(default=None, alias="monty:src_event_id")
# ImpactDetail
unit: str | None = None
```

## 4. Text fields

| Collection | median `description` chars | p90 | unique | `== "NA"` | contains HTML |
|---|---|---|---|---|---|
| emdat-events | 65 | 151 | 25,196 / 25,308 | 0 | 0 |
| emdat-impacts | 70 | 171 | 24,369 / 64,637 | 0 | 0 |
| gdacs-events | 63 | 78 | 30,427 / 43,723 | 0 | 0 |
| ifrcevent-events | **2** | 1,258 | 326 / 2,199 | **1,871 (85%)** | ~300 |
| ifrcevent-impacts | 503 | 4,367 | 218 / 1,349 | — | most |

Samples:

- EM-DAT: `Flood in Lushoto, Korogwe districts (Tanga region), United Republic of Tanzania of February 1993` —
  formulaic `<Hazard> in <Place>, <Country> of <Month Year>`. The information is already in
  `hazard_codes` / `country_codes` / `datetime`; the only novel content is the sub-national place name.
- GDACS: `Green M 4.6 Earthquake in Indonesia at: 28 Apr 2005 04:48:13.` — fully templated.
  Embeddings will cluster by template, not by event.
- IFRC: `NA` (85%), else raw HTML from a rich-text editor (`<p class="MsoNormal" style=...>`).
  The ~300 real ones are long (p90 1.3k chars, max 79k) and are the only free-form narrative in
  the five collections pulled. Need HTML stripping before any NLP.

Implication for the embedding track: `keywords` is embeddable on <1% of items, `description`
is templated on the two big collections. The signal for clustering is in the structured fields.

## 5. `monty:corr_id` linkage

- 56,715 unique `corr_id`s across 137k items.
- **Within-source:** every EM-DAT impact and every IFRC impact joins to an event on `corr_id` (100%).
- **Cross-source:** only 470 of 56,712 event `corr_id`s (0.8%) appear in more than one source
  collection. 327 link EM-DAT↔GDACS, 50 EM-DAT↔IFRC, 21 GDACS↔IFRC.

`corr_id` is effectively a per-source event key. Cross-source linkage will need something else —
`glide-events` (8.5k items, the GLIDE number is the sector's cross-reference key), or a fuzzy match
on `(country, hazard, date window)`.

## 6. Categoricals and geometry

- **Hazard codes:** 108,948 items carry one code, but 27,397 carry 3 or 5 — one per taxonomy.
  Top values mix EM-DAT (`nat-hyd-flo-riv`), UNDRR-ISC (`GH0001`, `EN0013`), and GLIDE (`FL`,
  `WF`) vocabularies in the same field.
- **Countries:** 98% single-country. Top: CHN, USA, IND, IDN, RUS.
- **Geometry:** GDACS = Point; IFRC = Polygon/MultiPolygon; EM-DAT = full-resolution country
  MultiPolygons, mean 33 KB, max 10.8 MB (`emdat-event-2002-0495-CHL`), >99% of the payload.
  Equivalent to `country_codes` joined to any country boundary set.
- **Impact types (EM-DAT):** `affected_total` 27k, `death` 20k, `injured` 8.6k, `cost` 5.8k,
  `displaced_total` 2.7k.
- **Date ranges:** EM-DAT 1900–2026; GDACS 2000–2026; IFRC 1970–2026.

## What the upstream repos tell us

Two IFRC repos explain most of the above. Neither is worth installing as a dependency; both are
worth reading and borrowing from.

### [`IFRCGo/montandon-etl`](https://github.com/IFRCGo/montandon-etl) (+ `pystac-monty` submodule)

The ETL that produces every item in the bank. Django/Celery, built by Togglecorp. STAC item
construction lives in `libs/pystac-monty`, which is the `pystac-monty 0.2.4` we see in
`processing:software`.

- **`keywords` is derived, not sourced.** Every source does
  `keywords = hazard_profiles.get_keywords(hazard_codes) + country_codes`. It is the hazard
  profile labels plus the country codes, restated as words. It carries no information that
  `monty:hazard_codes` + `monty:country_codes` don't, and it can be reconstructed for the 97% of
  items missing it.
- **`monty:corr_id` is deterministic:** `{YYYYMMDD}-{country}-{geoblock}-{hazard_cluster}-{episode}-GCDB`
  (`pystac_monty/paring.py`). Three format generations coexist in the bank:

  | Format | Example | Event IDs | Cross-source match |
  |---|---|---|---|
  | legacy (raw EM-DAT code, no geoblock) | `20161210-KEN-TEC-TRA-ROA-ROA-1-GCDB` | (in ↓) | — |
  | canonical cluster, no geoblock | `20251216-IDN-MH0600-1-GCDB` | 46,415 | 1.0% |
  | canonical cluster + geoblock (current) | `20260828-ZWE-638247-TL0405-1-GCDB` | 10,297 | **0.0%** |

  GDACS puts its own event ID in the `episode` slot (`…-GH0101-1732529-GCDB`); EM-DAT and IFRC
  put `1`. So a GDACS and an EM-DAT record of the same event can never share a `corr_id`, in any
  format. The geoblock in the current format makes matching stricter still.
- **`HazardProfiles.csv`** (303 rows) is the hazard-code crosswalk: UNDRR-2025 ↔ UNDRR ↔ GLIDE ↔
  EM-DAT keys, each with `cluster_label` and `family_label`. This is the normalisation table for
  `monty:hazard_codes`. Plain CSV, vendorable (check `LICENSE`).
- **`geo_blocks-0.2.parquet`** is the spatial grid used for the geoblock. Available if we want
  to replicate blocking.
- **`extension.py`** has the canonical field list and enums (`MontyRoles`, `MontyEstimateType`,
  `MontyImpactExposureCategory`, `MontyImpactType`, `MontyResponseType`) and a
  `monty:response_detail` object we haven't seen because no response collection has data yet.
  The enums could replace bare `str` in `api_schemas.py`. Schema URI:
  `monty-stac-extension/v1.3.0`.
- **"Separate historical and latest pipelines"** (CHANGELOG 1.0.0, 2026-06-30). History was
  backfilled by an older code path — which is why field presence splits by year. The stakeholder
  question is therefore: *is the historical pipeline going to be re-run on pystac-monty 0.2.4?*

### [`IFRCGo/montandon-notebooks`](https://github.com/IFRCGo/montandon-notebooks) ([cookbook site](https://ifrcgo.org/montandon-notebooks/))

Nine recipe notebooks from IFRC. Two things they settle:

- **IFRC does not use `corr_id` to link across sources either.** Recipe 07 (cascading impacts,
  Türkiye–Syria 2023) links the earthquake to aftershocks and landslides by `bbox` + `datetime`
  window + a hand-enumerated list of hazard codes across every vocabulary
  (`["GH0101", "EQ", "nat-geo-ear-gro", "GH0001", "GH0002"]`). `corr_id` is only used to walk
  event → hazard → impact *within* a source — consistent with our 100% within-source / ~0%
  cross-source finding. Our pairing approach should be the same: `(date ± tolerance, country,
  canonical cluster)`, with the crosswalk above replacing the hand-enumerated lists.
- **The API supports CQL2 filtering server-side** (Recipe 08): `a_overlaps` on
  `monty:hazard_codes`, `a_contains` on `monty:country_codes`, `t_intersects` on `datetime`,
  and comparisons on `monty:impact_detail.type` / `.value` and `monty:hazard_detail.cluster` /
  `.severity_value`. `/queryables` lists the filterable fields. This means targeted questions
  ("all flood impacts with deaths > 10 in 2024") don't need a full pull. Two caveats from the
  recipe: filter by *collection*, not `roles` ("server-side issues"), and pystac-client's search
  sometimes needs a raw-HTTP fallback "to handle the `/stac/` endpoint routing issue".
- **Per-source field mapping docs** exist at
  `github.com/IFRCGo/monty-stac-extension/tree/main/model/sources/<SOURCE>` (linked from each
  collection's `describedby`). That's where "which IFRC GO field becomes `description`" is
  answered — and probably why 85% of them are `NA`.
- Their example `corr_id` in the Getting Started table (`20241113-ESP-NAT-HYD-FLO-FLO-1-GCDB`)
  is the *legacy* format. The docs predate the current ETL.

## API behaviour worth knowing

- No `numberMatched` on any endpoint; counting means paging IDs with the `fields` extension.
- The staging server has a ~30 s gateway timeout and drops connections under load with no error.
  `pystac_client.Client.open()` accepts `timeout=`; without it a pull can hang indefinitely.
- Max page size is 10,000 but EM-DAT geometry makes anything over ~100/page time out. Page size
  has to be per-collection.
- `fields={"exclude": ["geometry"]}` is honoured server-side and cuts EM-DAT from ~1.5 GB to 10 MB.

## Collection census

See [`../data/collection_counts.json`](../data/collection_counts.json) (local only). Summary:

| Collection | Items | | Collection | Items |
|---|---|---|---|---|
| ibtracs-hazards | 722,837 | | idmc-idu-events | 21,161 |
| pdc-events | 164,587 | | idmc-gidd-events | 19,230 |
| emdat-impacts | 64,637 | | ibtracs-events | 13,467 |
| gdacs-hazards | 59,038 | | glide-events / -hazards | 8,523 each |
| gdacs-events | 43,595 | | ifrcevent-events | 2,199 |
| gdacs-impacts | 36,956 | | gfd-impacts | 1,826 |
| idmc-gidd-impacts | 35,668 | | ifrcevent-impacts | 1,349 |
| idmc-idu-impacts | 32,558 | | gfd-events / -hazards | 913 each |
| emdat-events / -hazards | 25,308 each | | cems-* (4), reference-events, rsh-test2-events, ifrcevent-hazards | 0 |

**Uncounted:** `usgs-events` (>1,000,000 — passed 1M IDs after 6.5 h of paging and was cancelled), `usgs-hazards`, `usgs-impacts`, `pdc-hazards`, `pdc-impacts`, `desinventar-events`, `desinventar-impacts`. All seven were already in the skip/sample tier; a CQL2 filter by year is the right way to size them if it ever matters.

**Counted total: 1,288,596 items** across 27 collections.
