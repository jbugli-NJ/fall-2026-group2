MATCH (event:Event)-[:IN_COUNTRY]->(:Country {code: $country_code})
WHERE EXISTS {
  MATCH (event)-[:HAS_HAZARD]->(matched_hazard:Hazard)
  WHERE $hazard_code_name_map[matched_hazard.code] = $hazard_name
}
  AND ($from_date IS NULL OR date(event.start_datetime) >= date($from_date))
  AND ($to_date IS NULL OR date(event.start_datetime) <= date($to_date))
WITH DISTINCT event
MATCH (article:NewsArticle)-[link:VERIFIED_FOR]->(event)
RETURN event.id AS event_id,
       article.title AS title,
       article.url AS url,
       article.source_name AS source_name,
       article.published_at AS published_at,
       link.evidence_excerpt AS evidence_excerpt,
       substring(article.body_text, 0, 3000) AS body_excerpt
ORDER BY event_id, published_at, url
