"""Order news candidates by relevance without filtering or verifying them."""

from collections.abc import Sequence
from functools import lru_cache

from sentence_transformers import CrossEncoder

from monty_tool.news.schemas import NewsArticle


@lru_cache(maxsize=1)
def _get_ranker() -> CrossEncoder:
    """Load the ranking model once per Python process, when first needed."""
    return CrossEncoder("cross-encoder/ms-marco-MiniLM-L6-v2", device="cpu")


def rank_news_articles(
    articles: Sequence[NewsArticle],
    *,
    reference_text: str,
) -> list[NewsArticle]:
    """Return all candidates in relevance order, leaving the input unchanged.

    reference_text describes the requested news or disaster event in English.
    Ranking uses titles and descriptions; it does not verify event identity.
    """
    reference_text = reference_text.strip()
    if not reference_text:
        raise ValueError("Provide a non-empty news search purpose.")

    candidates = list(articles)
    if len(candidates) < 2:
        return candidates

    pairs = [
        (reference_text, f"{article.title}\n{article.description or ''}")
        for article in candidates
    ]
    scores = _get_ranker().predict(pairs, show_progress_bar=False)
    scored = list(zip(candidates, scores, strict=True))
    scored.sort(key=lambda item: float(item[1]), reverse=True)
    return [article for article, _ in scored]
