"""
Utilities to generate embeddings from Montandon records.
"""

# Imports

from sentence_transformers import SentenceTransformer

from monty_tool.api_schemas import MontandonImpactProperties, MontandonItem
from monty_tool.embeddings.schemas import MontandonItemEmbeddings, Embedding


# Constants

EMBEDDING_MODEL_NAME = 'all-MiniLM-L6-v2'


# Helpers

def _text(value: str) -> str | None:
    """
    Return stripped text or None if blank.
    """
    value = value.strip()
    return value or None

def _keywords(values: list[str]) -> str | None:
    """
    Join keywords into one string for embedding generation.
    """
    text = '\n'.join(value.strip() for value in values if value.strip())
    return text or None


# Text extraction

def extract_field_texts(item: MontandonItem) -> dict[str, str]:
    """
    Extract text for fields with corresponding embedding attributes.
    """
    properties = item.properties
    texts: dict[str, str | None] = {
        'title': properties.title,
        'description': properties.description,
        'keywords': _keywords(properties.keywords),
        'impact_severity_text': None,
    }
    if (
        isinstance(properties, MontandonImpactProperties)
        and properties.severitydata is not None
    ):
        texts['impact_severity_text'] = _text(properties.severitydata.severitytext)
    return {field: text for field, text in texts.items() if text is not None}


# Embedding generation

def _generate_item_embeddings(
    item: MontandonItem,
    model: SentenceTransformer,
    ) -> MontandonItemEmbeddings:
    """
    Generate embedding vectors for one Montandon record.
    """
    texts = extract_field_texts(item)
    vectors = model.encode(
        list(texts.values()),
        convert_to_numpy=True,
        normalize_embeddings=True,
        show_progress_bar=False,
    ).tolist()
    if len(vectors) != len(texts):
        raise ValueError("The model must return one vector per text field")
    vector_mapping: dict[str, Embedding] = dict(zip(texts, vectors, strict=True))
    return MontandonItemEmbeddings(
        item_id=item.id,
        title=vector_mapping['title'],
        description=vector_mapping['description'],
        keywords=vector_mapping.get('keywords'),
        impact_severity_text=vector_mapping.get('impact_severity_text'),
    )


def generate_embeddings(
    items: list[MontandonItem],
    ) -> list[MontandonItemEmbeddings]:
    """
    Generate embedding vectors for a list of Montandon records.
    """
    if not items:
        return []
    model = SentenceTransformer(EMBEDDING_MODEL_NAME)
    return [
        _generate_item_embeddings(item, model)
        for item in items
    ]
