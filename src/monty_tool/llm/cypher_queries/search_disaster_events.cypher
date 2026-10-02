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
  AND ($min_elevation IS NULL OR event.weather_elevation >= $min_elevation)
  AND ($max_elevation IS NULL OR event.weather_elevation <= $max_elevation)
  AND ($min_mean_temperature IS NULL OR event.weather_mean_temperature >= $min_mean_temperature)
  AND ($max_mean_temperature IS NULL OR event.weather_mean_temperature <= $max_mean_temperature)
  AND ($min_precipitation_total IS NULL OR event.weather_observed_precipitation_total >= $min_precipitation_total)
  AND ($max_precipitation_total IS NULL OR event.weather_observed_precipitation_total <= $max_precipitation_total)
OPTIONAL MATCH (event)-[:IN_COUNTRY]->(country:Country)
OPTIONAL MATCH (event)-[:HAS_HAZARD]->(hazard:Hazard)
RETURN event.id AS event_id,
       event.title AS title,
       event.start_datetime AS start_datetime,
       event.end_datetime AS end_datetime,
       CASE WHEN event.weather_source IS NULL THEN NULL ELSE {
         elevation: event.weather_elevation,
         elevation_unit: event.weather_elevation_unit,
         mean_temperature: event.weather_mean_temperature,
         temperature_mean_unit: event.weather_temperature_mean_unit,
         observed_precipitation_total: event.weather_observed_precipitation_total,
         observed_precipitation_total_unit: event.weather_observed_precipitation_total_unit,
         start_date: event.weather_start_date,
         end_date: event.weather_end_date,
         expected_days: event.weather_expected_days,
         temperature_mean_measured_days: event.weather_temperature_mean_measured_days,
         precipitation_measured_days: event.weather_precipitation_measured_days
       } END AS weather,
       collect(DISTINCT country.code) AS country_codes,
       collect(DISTINCT hazard.code) AS hazard_codes
ORDER BY event.start_datetime DESC
LIMIT 10
