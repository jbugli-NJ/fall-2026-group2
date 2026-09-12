"""
Schemas for embeddings generated from Montandon records.
"""

# Imports

from dataclasses import dataclass


# Types

Embedding = list[float]


# Schema

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
