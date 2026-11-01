MATCH (event:Event)
WHERE ($country_code IS NULL OR EXISTS {
        MATCH (event)-[:IN_COUNTRY]->(:Country {code: $country_code})
      })
  AND ($from_date IS NULL OR date(event.start_datetime) >= $from_date)
  AND ($to_date IS NULL OR date(event.start_datetime) <= $to_date)
  AND (NOT $require_external_data OR (
        (NOT ('weather' IN $external_sources) OR event.weather_source IS NOT NULL OR EXISTS {
          MATCH (impact:Impact)-[:IMPACT_OF]->(event)
          WHERE impact.weather_source IS NOT NULL
        })
        AND (NOT ('news' IN $external_sources) OR EXISTS {
          MATCH (:NewsArticle)-[:VERIFIED_FOR]->(event)
        })
        AND (NOT ('shakemap_footprints' IN $external_sources) OR EXISTS {
          MATCH (:Footprint {origin: 'external'})-[:FOOTPRINT_OF]->(event)
        })
      ))
WITH event
ORDER BY event.start_datetime DESC, event.id ASC
SKIP $offset
LIMIT $limit
OPTIONAL MATCH (event)-[:IN_COUNTRY]->(country:Country)
OPTIONAL MATCH (event)-[:HAS_HAZARD]->(hazard:Hazard)
OPTIONAL MATCH (impact:Impact)-[:IMPACT_OF]->(event)
RETURN event.id AS event_id,
       event.title AS title,
       event.description AS description,
       toString(event.start_datetime) AS start_datetime,
       toString(event.end_datetime) AS end_datetime,
       event.corr_id AS correlation_id,
       collect(DISTINCT country.code) AS country_codes,
       collect(DISTINCT hazard.code) AS hazard_codes,
       collect(DISTINCT impact {
         impact_id: impact.id,
         .title,
         .description,
         .impact_type,
         .impact_category,
         .impact_value,
         .impact_unit,
         estimate_type: impact.impact_estimate_type
       }) AS impacts
ORDER BY start_datetime DESC, event_id ASC
