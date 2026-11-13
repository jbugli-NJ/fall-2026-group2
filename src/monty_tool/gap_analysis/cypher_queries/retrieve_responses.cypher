MATCH (event:GOEvent)-[:IN_COUNTRY]->(:Country {code: $country_code})
WHERE event.disaster_type_id IN $response_hazard_ids
  AND ($from_date IS NULL OR date(event.start_datetime) >= date($from_date))
  AND ($to_date IS NULL OR date(event.start_datetime) <= date($to_date))
WITH DISTINCT event
ORDER BY event.start_datetime IS NULL, event.start_datetime DESC, event.id
LIMIT $disaster_limit
OPTIONAL MATCH (appeal:GOAppeal)-[:FOR_EVENT]->(event)
WITH event, appeal
ORDER BY appeal.start_datetime, appeal.id
WITH event, collect(CASE WHEN appeal IS NULL THEN NULL ELSE {
  appeal_id: appeal.id,
  title: appeal.title,
  code: appeal.code,
  sector: appeal.sector,
  country_name: appeal.country_name,
  country_society_name: appeal.country_society_name,
  status: appeal.status_display,
  appeal_type: appeal.appeal_type_display,
  beneficiaries: appeal.num_beneficiaries,
  amount_requested: appeal.amount_requested,
  amount_funded: appeal.amount_funded,
  start_datetime: appeal.start_datetime,
  end_datetime: appeal.end_datetime
} END) AS appeals
RETURN event.id AS event_id,
       event.title AS title,
       event.disaster_type_name AS disaster_type,
       event.start_datetime AS start_datetime,
       event.summary AS summary,
       event.ifrc_severity_level_display AS severity,
       event.num_affected AS people_affected,
       event.active_deployments AS active_deployments,
       appeals

ORDER BY start_datetime IS NULL, start_datetime DESC, event_id
