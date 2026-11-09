"""
Fallback hazard names missing from the available data based on descriptions
and other sources where available.

Names follow https://ifrcgo.org/monty-stac-extension/model/taxonomy/,
except where an upstream source mapping is cited below.
"""

HAZARD_NAME_FALLBACKS: dict[str, str] = {
    'GH0002': 'Earthquake',
    'GH0006': 'Earthquake-triggered tsunami',
    'GH0007': 'Earthquake-triggered landslide or debris flow',
    'GH0014': 'Volcano-triggered landslide',
    'GH0015': 'Volcanic earthquake ground shaking',
    'GH009': 'Volcanic eruption',
    'TL0005': 'Building Collapse',
    'TL0029': 'Industrial explosion',
    'TL0032': 'Industrial fire',
    'TL0048': 'Air transport accident',
    'TL0049': 'Inland waterway accident',
    'TL0051': 'Rail Accident',
    'TL0052': 'Road Traffic Accident',
    'nat-gem-ear-gro': 'Earthquake',
    'tec-ind-col-col': 'Industrial collapse',
    'tec-mis-exp-exp': 'Miscellaneous explosion',
}
