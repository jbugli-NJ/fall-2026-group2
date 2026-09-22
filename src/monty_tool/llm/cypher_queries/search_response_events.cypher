MATCH (event:GOEvent)
WHERE ($country_code IS NULL OR EXISTS {
        MATCH (event)-[:IN_COUNTRY]->(:Country {code: $country_code})
      })
  AND ($disaster_type IS NULL
       OR toLower(event.disaster_type_name) CONTAINS toLower($disaster_type))
  AND ($from_date IS NULL OR date(event.start_datetime) >= $from_date)
  AND ($to_date IS NULL OR date(event.start_datetime) <= $to_date)
  AND ($text IS NULL
       OR toLower(event.title) CONTAINS toLower($text)
       OR toLower(event.summary) CONTAINS toLower($text))
OPTIONAL MATCH (event)-[:IN_COUNTRY]->(country:Country)
RETURN event.id AS event_id,
       event.title AS title,
       event.disaster_type_name AS disaster_type,
       event.start_datetime AS start_datetime,
       event.ifrc_severity_level AS severity_level,
       event.ifrc_severity_level_display AS severity,
       event.num_affected AS people_affected,
       event.active_deployments AS active_deployments,
       collect(DISTINCT country.code) AS country_codes
ORDER BY event.start_datetime DESC
LIMIT 10
