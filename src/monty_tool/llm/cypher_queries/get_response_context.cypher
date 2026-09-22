MATCH (event:GOEvent {id: $event_id})
OPTIONAL MATCH (appeal:GOAppeal)-[:FOR_EVENT]->(event)
WITH event, collect(CASE WHEN appeal IS NULL THEN NULL ELSE {
  appeal_id: appeal.id,
  title: appeal.title,
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
       event.ifrc_severity_level AS severity_level,
       event.ifrc_severity_level_display AS severity,
       event.num_affected AS people_affected,
       event.active_deployments AS active_deployments,
       appeals
