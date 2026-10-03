MATCH (article:NewsArticle)-[link:RETRIEVED_FOR]->(event:Event {id: $event_id})
RETURN event.id AS event_id,
       article.title AS title,
       article.description AS description,
       article.url AS url,
       article.source_name AS source_name,
       article.published_at AS published_at,
       link.snapshot_s3_uris AS snapshot_s3_uris
ORDER BY published_at DESC, url
LIMIT 20

