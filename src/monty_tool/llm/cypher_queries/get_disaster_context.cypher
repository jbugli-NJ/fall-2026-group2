MATCH (event:Event {id: $event_id})
OPTIONAL MATCH (event)-[:IN_COUNTRY]->(country:Country)
OPTIONAL MATCH (event)-[:HAS_HAZARD]->(hazard:Hazard)
OPTIONAL MATCH (impact:Impact)-[:IMPACT_OF]->(event)
RETURN event.id AS event_id,
       event.title AS title,
       event.description AS description,
       event.start_datetime AS start_datetime,
       event.end_datetime AS end_datetime,
       event.corr_id AS correlation_id,
       collect(DISTINCT country.code) AS country_codes,
       collect(DISTINCT hazard.code) AS hazard_codes,
       collect(DISTINCT CASE WHEN impact IS NULL THEN NULL ELSE {
         impact_id: impact.id,
         title: impact.title,
         description: impact.description,
         impact_type: impact.impact_type,
         impact_category: impact.impact_category,
         impact_value: impact.impact_value,
         impact_unit: impact.impact_unit,
         estimate_type: impact.impact_estimate_type
       } END) AS impacts
