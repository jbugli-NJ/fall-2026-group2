"""
Prepare network data from Montandon records.
"""

# Imports

from monty_tool.api_schemas import (
    GOAppeal,
    GOEvent,
    MontandonImpactProperties,
    MontandonItem,
)
from monty_tool.embeddings.generate import generate_embeddings
from monty_tool.network.schemas import (
    GOAppealNodeData,
    GOEventNodeData,
    MontandonItemNodeData,
)


# Node data helper

def montandon_items_to_node_data(
    items: list[MontandonItem],
    ) -> list[MontandonItemNodeData]:
    """
    Generate embeddings and network graph data from Montandon records.
    """
    embeddings = generate_embeddings(items=items)
    node_data: list[MontandonItemNodeData] = []
    for item, embedding in zip(items, embeddings, strict=True):
        if item.id != embedding.item_id:
            raise ValueError(
                f'Embedding ID {embedding.item_id!r} does not match '
                f'Item ID {item.id!r}'
            )
        properties = item.properties
        node: MontandonItemNodeData = {
            'id': item.id,
            'title': properties.title,
            'description': properties.description,
            'roles': properties.roles,
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


def go_events_to_node_data(events: list[GOEvent]) -> list[GOEventNodeData]:
    """
    Prepare IFRC GO events for graph insertion.
    """
    node_data: list[GOEventNodeData] = []
    for event in events:
        disaster_type = event.dtype
        node_data.append({
            'id': f'go-event-{event.id}',
            'title': event.name,
            'go_event_id': event.id,
            'disaster_type_id': disaster_type.id if disaster_type else None,
            'disaster_type_name': disaster_type.name if disaster_type else None,
            'country_codes': sorted({
                country.iso3
                for country in event.countries
                if country.iso3 is not None
            }),
            'country_names': sorted({country.name for country in event.countries}),
            'num_affected': event.num_affected,
            'ifrc_severity_level': event.ifrc_severity_level,
            'ifrc_severity_level_display': event.ifrc_severity_level_display,
            'ifrc_severity_level_update_date': (
                event.ifrc_severity_level_update_date
            ),
            'glide': event.glide,
            'start_datetime': event.disaster_start_date,
            'created_at': event.created_at,
            'updated_at': event.updated_at,
            'active_deployments': event.active_deployments,
            'summary': event.summary,
            'original_language': event.translation_module_original_language,
        })
    return node_data


def go_appeals_to_node_data(appeals: list[GOAppeal]) -> list[GOAppealNodeData]:
    """
    Prepare IFRC GO appeals for graph insertion.
    """
    node_data: list[GOAppealNodeData] = []
    for appeal in appeals:
        node_data.append({
            'id': f'go-appeal-{appeal.id}',
            'title': appeal.name,
            'go_appeal_id': appeal.id,
            'aid': appeal.aid,
            'go_event_id': appeal.event,
            'disaster_type_id': appeal.dtype.id,
            'disaster_type_name': appeal.dtype.name,
            'appeal_type': appeal.atype,
            'appeal_type_display': appeal.atype_display,
            'status': appeal.status,
            'status_display': appeal.status_display,
            'code': appeal.code,
            'sector': appeal.sector,
            'num_beneficiaries': appeal.num_beneficiaries,
            'amount_requested': appeal.amount_requested,
            'amount_funded': appeal.amount_funded,
            'start_datetime': appeal.start_date,
            'end_datetime': appeal.end_date,
            'real_data_update': appeal.real_data_update,
            'created_at': appeal.created_at,
            'modified_at': appeal.modified_at,
            'needs_confirmation': appeal.needs_confirmation,
            'country_code': appeal.country.iso3,
            'country_name': appeal.country.name,
            'country_go_id': appeal.country.id,
            'country_fdrs': appeal.country.fdrs,
            'country_society_name': appeal.country.society_name,
            'region_go_id': appeal.region.id,
            'region_name': appeal.region.region_name,
        })
    return node_data
