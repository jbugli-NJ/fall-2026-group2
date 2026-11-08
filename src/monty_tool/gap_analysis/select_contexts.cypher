MATCH (event:Event)-[:IN_COUNTRY]->(country:Country)
MATCH (event)-[:HAS_HAZARD]->(hazard:Hazard)
WHERE ($country_code IS NULL OR country.code = $country_code)
  AND ($hazard_name IS NULL OR $hazard_code_name_map[hazard.code] = $hazard_name)
  AND $hazard_code_name_map[hazard.code] IS NOT NULL
  AND ($from_date IS NULL OR date(event.start_datetime) >= $from_date)
  AND ($to_date IS NULL OR date(event.start_datetime) <= $to_date)
WITH country, $hazard_code_name_map[hazard.code] AS hazard_name,
     collect(DISTINCT event) AS events,
     max(event.start_datetime) AS last_event_start
WHERE NOT $require_external_data OR (
  (NOT ('weather' IN $external_sources) OR any(event IN events WHERE
    event.weather_source IS NOT NULL OR EXISTS {
      MATCH (impact:Impact)-[:IMPACT_OF]->(event)
      WHERE impact.weather_source IS NOT NULL
    }
  ))
  AND (NOT ('news' IN $external_sources) OR any(event IN events WHERE EXISTS {
    MATCH (:NewsArticle)-[:VERIFIED_FOR]->(event)
  }))
  AND (NOT ('shakemap_footprints' IN $external_sources) OR any(event IN events WHERE EXISTS {
    MATCH (:Footprint {origin: 'external'})-[:FOOTPRINT_OF]->(event)
  }))
)
RETURN country.code AS country_code,
       hazard_name,
       toString($from_date) AS from_date,
       toString($to_date) AS to_date
ORDER BY last_event_start DESC, country_code ASC, hazard_name ASC
SKIP $offset
LIMIT $limit
