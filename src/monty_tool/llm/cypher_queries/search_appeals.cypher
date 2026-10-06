MATCH (appeal:GOAppeal)
WHERE ($country_code IS NULL OR appeal.country_code = $country_code)
  AND ($disaster_type IS NULL
       OR toLower(appeal.disaster_type_name) CONTAINS toLower($disaster_type))
  AND ($from_date IS NULL OR date(appeal.start_datetime) >= $from_date)
  AND ($to_date IS NULL OR date(appeal.start_datetime) <= $to_date)
  AND ($text IS NULL OR toLower(appeal.title) CONTAINS toLower($text))
OPTIONAL MATCH (appeal)-[:FOR_EVENT]->(event:GOEvent)
RETURN appeal.id AS appeal_id,
       appeal.title AS title,
       appeal.disaster_type_name AS disaster_type,
       appeal.status_display AS status,
       appeal.appeal_type_display AS appeal_type,
       appeal.num_beneficiaries AS beneficiaries,
       appeal.amount_requested AS amount_requested,
       appeal.amount_funded AS amount_funded,
       appeal.start_datetime AS start_datetime,
       appeal.end_datetime AS end_datetime,
       appeal.country_code AS country_code,
       appeal.country_name AS country_name,
       event.id AS event_id
ORDER BY appeal.start_datetime DESC, appeal.id
LIMIT 10
