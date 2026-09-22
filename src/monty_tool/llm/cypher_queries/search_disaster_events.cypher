MATCH (event:Event)
WHERE ($country_code IS NULL OR EXISTS {
        MATCH (event)-[:IN_COUNTRY]->(:Country {code: $country_code})
      })
  AND ($hazard_code IS NULL OR EXISTS {
        MATCH (event)-[:HAS_HAZARD]->(:Hazard {code: $hazard_code})
      })
  AND ($from_date IS NULL OR date(event.start_datetime) >= $from_date)
  AND ($to_date IS NULL OR date(event.start_datetime) <= $to_date)
  AND ($text IS NULL
       OR toLower(event.title) CONTAINS toLower($text)
       OR toLower(event.description) CONTAINS toLower($text))
OPTIONAL MATCH (event)-[:IN_COUNTRY]->(country:Country)
OPTIONAL MATCH (event)-[:HAS_HAZARD]->(hazard:Hazard)
RETURN event.id AS event_id,
       event.title AS title,
       event.start_datetime AS start_datetime,
       event.end_datetime AS end_datetime,
       collect(DISTINCT country.code) AS country_codes,
       collect(DISTINCT hazard.code) AS hazard_codes
ORDER BY event.start_datetime DESC
LIMIT 10
