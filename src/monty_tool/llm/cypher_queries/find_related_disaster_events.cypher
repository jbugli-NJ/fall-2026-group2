MATCH (event:Event {id: $event_id})
CALL {
  WITH event
  MATCH (event)-[relationship:SIMILAR_TO|SIMILAR_KEYWORDS|SIMILAR_IMPACT]-(related:Event)
  WHERE $relation_kind = 'semantic_similarity'
  RETURN related.id AS event_id, related.title AS title,
         related.start_datetime AS start_datetime,
         'semantic_similarity' AS relationship_kind,
         type(relationship) AS relationship_detail,
         relationship.similarity AS similarity
  UNION ALL
  WITH event
  MATCH (event)-[:HAS_HAZARD]->(hazard:Hazard)<-[:HAS_HAZARD]-(related:Event)
  WHERE $relation_kind = 'same_hazard' AND related.id <> event.id
  RETURN related.id, related.title, related.start_datetime,
         'same_hazard', hazard.code, NULL
  UNION ALL
  WITH event
  MATCH (event)-[:IN_COUNTRY]->(country:Country)<-[:IN_COUNTRY]-(related:Event)
  WHERE $relation_kind = 'same_country' AND related.id <> event.id
  RETURN related.id, related.title, related.start_datetime,
         'same_country', country.code, NULL
  UNION ALL
  WITH event
  MATCH (event)-[:SAME_DAY_START]-(related:Event)
  WHERE $relation_kind = 'same_start_day'
  RETURN related.id, related.title, related.start_datetime,
         'same_start_day', 'SAME_DAY_START', NULL
  UNION ALL
  WITH event
  MATCH (related:Event {corr_id: event.corr_id})
  WHERE $relation_kind = 'same_incident' AND related.id <> event.id
  RETURN related.id, related.title, related.start_datetime,
         'same_incident', event.corr_id, NULL
}
RETURN DISTINCT event_id, title, start_datetime,
       relationship_kind, relationship_detail, similarity
LIMIT 10
