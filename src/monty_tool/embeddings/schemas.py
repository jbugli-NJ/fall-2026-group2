"""
Schemas for embeddings generated from Montandon records.
"""

# Imports

from dataclasses import dataclass

import polars as pl


# Types

Embedding = list[float]


# Schema

PL_EMBEDDINGS_SCHEMA = pl.Schema({
    'item_id': pl.String,
    'title': pl.List(pl.Float32),
    'description': pl.List(pl.Float32),
    'keywords': pl.List(pl.Float32),
    'impact_severity_text': pl.List(pl.Float32),
})


@dataclass
class MontandonItemEmbeddings:
    """
    Embeddings tied to one `MontandonItem` instance.
    """
    item_id: str
    title: Embedding
    description: Embedding
    keywords: Embedding | None = None
    impact_severity_text: Embedding | None = None

    def to_dataframe(self) -> pl.DataFrame:
        """
        Return this record as a Polars DataFrame.
        """
        return pl.DataFrame(
            {
                'item_id': [self.item_id],
                'title': [self.title],
                'description': [self.description],
                'keywords': [self.keywords],
                'impact_severity_text': [self.impact_severity_text],
            },
            schema=PL_EMBEDDINGS_SCHEMA,
        )
