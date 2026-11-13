MATCH (event:Event)-[:IN_COUNTRY]->(country:Country)
MATCH (event)-[:HAS_HAZARD]->(hazard:Hazard)
WHERE ($country_code IS NULL OR country.code = $country_code)
  AND country.code <> 'UNK'
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
RETURN {
         country_code: country.code,
         from_date: toString($from_date),
         to_date: toString($to_date)
       } AS filters,
       hazard_name
ORDER BY last_event_start DESC, country.code ASC, hazard_name ASC
SKIP $offset
LIMIT $limit
