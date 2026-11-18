MATCH (event:Event)
WHERE event.id IN $event_ids
MATCH (article:NewsArticle)-[link:VERIFIED_FOR]->(event)
RETURN event.id AS event_id,
       article.title AS title,
       article.url AS url,
       article.source_name AS source_name,
       article.published_at AS published_at,
       link.evidence_excerpt AS evidence_excerpt,
       substring(article.body_text, 0, 3000) AS body_excerpt
ORDER BY event_id, published_at, url
