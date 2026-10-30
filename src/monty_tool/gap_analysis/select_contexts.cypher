MATCH (event:Event)
WHERE ($country_code IS NULL OR EXISTS {
        MATCH (event)-[:IN_COUNTRY]->(:Country {code: $country_code})
      })
  AND ($from_date IS NULL OR date(event.start_datetime) >= $from_date)
  AND ($to_date IS NULL OR date(event.start_datetime) <= $to_date)
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
