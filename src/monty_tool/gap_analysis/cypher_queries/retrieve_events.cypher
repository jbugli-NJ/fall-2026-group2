MATCH (event:Event)-[:IN_COUNTRY]->(:Country {code: $country_code})
WHERE EXISTS {
  MATCH (event)-[:HAS_HAZARD]->(matched_hazard:Hazard)
  WHERE $hazard_code_name_map[matched_hazard.code] = $hazard_name
}
  AND ($from_date IS NULL OR date(event.start_datetime) >= date($from_date))
  AND ($to_date IS NULL OR date(event.start_datetime) <= date($to_date))
  AND (
    ($external_source = 'news' AND EXISTS {
      MATCH (:NewsArticle)-[:VERIFIED_FOR]->(event)
    })
    OR ($external_source = 'weather' AND (
      event.weather_source IS NOT NULL OR EXISTS {
        MATCH (impact:Impact)-[:IMPACT_OF]->(event)
        WHERE impact.weather_source IS NOT NULL
      }
    ))
    OR ($external_source = 'shakemap_footprints' AND EXISTS {
      MATCH (:Footprint {origin: 'external'})-[:FOOTPRINT_OF]->(event)
    })
  )
WITH DISTINCT event
ORDER BY event.start_datetime IS NULL, event.start_datetime DESC, event.id
LIMIT $disaster_limit
CALL (event) {
  MATCH (event)-[:IN_COUNTRY]->(country:Country)
  WITH DISTINCT country.code AS code ORDER BY code
  RETURN collect(code) AS country_codes
}
CALL (event) {
  MATCH (event)-[:HAS_HAZARD]->(hazard:Hazard)
  WITH DISTINCT hazard.code AS code ORDER BY code
  RETURN collect(code) AS hazard_codes
}
CALL (event) {
  MATCH (impact:Impact)-[:IMPACT_OF]->(event)
  WITH DISTINCT impact ORDER BY impact.id
  RETURN collect(DISTINCT {
         impact_id: impact.id,
         title: impact.title,
         description: impact.description,
         impact_type: impact.impact_type,
         impact_category: impact.impact_category,
         impact_value: impact.impact_value,
         impact_unit: impact.impact_unit,
         estimate_type: impact.impact_estimate_type,
         weather: CASE WHEN impact.weather_source IS NULL THEN NULL ELSE {
           source: impact.weather_source,
           sources: impact.weather_sources,
           latitude: impact.weather_latitude,
           longitude: impact.weather_longitude,
           elevation: impact.weather_elevation,
           elevation_unit: impact.weather_elevation_unit,
           start_date: impact.weather_start_date,
           end_date: impact.weather_end_date,
           expected_days: impact.weather_expected_days,
           precipitation_measured_days: impact.weather_precipitation_measured_days,
           temperature_mean_measured_days: impact.weather_temperature_mean_measured_days,
           temperature_max_measured_days: impact.weather_temperature_max_measured_days,
           temperature_min_measured_days: impact.weather_temperature_min_measured_days,
           wind_speed_measured_days: impact.weather_wind_speed_measured_days,
           observed_precipitation_total: impact.weather_observed_precipitation_total,
           peak_daily_precipitation: impact.weather_peak_daily_precipitation,
           peak_daily_precipitation_date: impact.weather_peak_daily_precipitation_date,
           mean_temperature: impact.weather_mean_temperature,
           max_temperature: impact.weather_max_temperature,
           min_temperature: impact.weather_min_temperature,
           max_daily_mean_wind_speed: impact.weather_max_daily_mean_wind_speed,
           precipitation_unit: impact.weather_precipitation_unit,
           observed_precipitation_total_unit: impact.weather_observed_precipitation_total_unit,
           temperature_mean_unit: impact.weather_temperature_mean_unit,
           temperature_max_unit: impact.weather_temperature_max_unit,
           temperature_min_unit: impact.weather_temperature_min_unit,
           wind_speed_unit: impact.weather_wind_speed_unit
         } END
       }) AS impacts
}
CALL (event) {
  MATCH (footprint:Footprint)-[:FOOTPRINT_OF]->(event)
  WITH DISTINCT footprint ORDER BY footprint.id
  RETURN collect(DISTINCT CASE WHEN footprint.origin = 'montandon' THEN {
         source: footprint.source,
         layer: footprint.layer,
         label: footprint.label,
         detail: footprint.detail,
         area_name: footprint.area_name,
         size_km: footprint.size_km,
         severity_property: footprint.severity_property,
         severity_levels: footprint.severity_levels,
         data_url: footprint.data_url
       } END) AS footprints,
       collect(DISTINCT CASE WHEN footprint.origin = 'external' THEN {
         source: footprint.source,
         layer: footprint.layer,
         label: footprint.label,
         detail: footprint.detail,
         area_name: footprint.area_name,
         size_km: footprint.size_km,
         severity_property: footprint.severity_property,
         severity_levels: footprint.severity_levels,
         data_url: footprint.data_url
       } END) AS external_footprints
}
RETURN event.id AS event_id,
       event.title AS title,
       event.description AS description,
       event.start_datetime AS start_datetime,
       event.end_datetime AS end_datetime,
       event.corr_id AS correlation_id,
       CASE WHEN event.weather_source IS NULL THEN NULL ELSE {
         source: event.weather_source,
         sources: event.weather_sources,
         latitude: event.weather_latitude,
         longitude: event.weather_longitude,
         elevation: event.weather_elevation,
         elevation_unit: event.weather_elevation_unit,
         start_date: event.weather_start_date,
         end_date: event.weather_end_date,
         expected_days: event.weather_expected_days,
         precipitation_measured_days: event.weather_precipitation_measured_days,
         temperature_mean_measured_days: event.weather_temperature_mean_measured_days,
         temperature_max_measured_days: event.weather_temperature_max_measured_days,
         temperature_min_measured_days: event.weather_temperature_min_measured_days,
         wind_speed_measured_days: event.weather_wind_speed_measured_days,
         observed_precipitation_total: event.weather_observed_precipitation_total,
         peak_daily_precipitation: event.weather_peak_daily_precipitation,
         peak_daily_precipitation_date: event.weather_peak_daily_precipitation_date,
         mean_temperature: event.weather_mean_temperature,
         max_temperature: event.weather_max_temperature,
         min_temperature: event.weather_min_temperature,
         max_daily_mean_wind_speed: event.weather_max_daily_mean_wind_speed,
         precipitation_unit: event.weather_precipitation_unit,
         observed_precipitation_total_unit: event.weather_observed_precipitation_total_unit,
         temperature_mean_unit: event.weather_temperature_mean_unit,
         temperature_max_unit: event.weather_temperature_max_unit,
         temperature_min_unit: event.weather_temperature_min_unit,
         wind_speed_unit: event.weather_wind_speed_unit
       } END AS weather,
       country_codes,
       hazard_codes,
       footprints,
       external_footprints,
       impacts
ORDER BY start_datetime IS NULL, start_datetime DESC, event_id
