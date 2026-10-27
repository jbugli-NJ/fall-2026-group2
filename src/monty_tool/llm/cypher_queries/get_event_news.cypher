MATCH (article:NewsArticle)-[link:VERIFIED_FOR]->(event:Event {id: $event_id})
WITH article, link, event
ORDER BY article.published_at DESC,
         article.url
LIMIT 20
RETURN event.id AS event_id,
       type(link) AS relationship_type,
       article.title AS title,
       article.url AS url,
       article.source_name AS source_name,
       article.published_at AS published_at,
       link.decision_reason AS decision_reason,
       link.evidence_excerpt AS evidence_excerpt,
       substring(article.body_text, 0, 3000) AS body_excerpt,
       link.snapshot_s3_uris AS snapshot_s3_uris