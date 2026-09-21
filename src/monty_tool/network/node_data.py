"""
Prepare network data from Montandon records.
"""

# Imports

from monty_tool.api_schemas import MontandonImpactProperties, MontandonItem
from monty_tool.embeddings.generate import generate_embeddings
from monty_tool.network.schemas import NodeData


# Node data helper

def items_to_node_data(items: list[MontandonItem]) -> list[NodeData]:
    """
    Generate embeddings and network graph data from Montandon records.
    """
    embeddings = generate_embeddings(items=items)
    node_data: list[NodeData] = []
    for item, embedding in zip(items, embeddings, strict=True):
        if item.id != embedding.item_id:
            raise ValueError(
                f'Embedding ID {embedding.item_id!r} does not match '
                f'Item ID {item.id!r}'
            )
        properties = item.properties
        node: NodeData = {
            'id': item.id,
            'title': properties.title,
            'description': properties.description,
            'description_embedding': embedding.description,
            'keywords_embedding': embedding.keywords,
            'impact_severity_embedding': embedding.impact_severity_text,
            'corr_id': properties.monty_corr_id,
            'country_codes': sorted(set(properties.monty_country_codes)),
            'hazard_codes': sorted(set(properties.monty_hazard_codes)),
            'start_datetime': properties.start_datetime,
            'end_datetime': properties.end_datetime,
        }
        if isinstance(properties, MontandonImpactProperties):
            impact = properties.monty_impact_detail
            node.update({
                'impact_type': impact.type,
                'impact_value': impact.value,
                'impact_category': impact.category,
                'impact_estimate_type': impact.estimate_type,
            })
            if impact.unit is not None:
                node['impact_unit'] = impact.unit
        node_data.append(node)
    return node_data
