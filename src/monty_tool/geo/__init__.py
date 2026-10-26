"""
Hazard footprints: where a hazard actually hit, from the sources that map
it in detail (CEMS layers, USGS ShakeMap, GDACS polygons, ...).

Each source has its own adapter module returning `Footprint` records, so
callers can rank and display footprints from different sources the same
way. `monty:corr_id` only links items within one source, so matching a
footprint to an event from another source is left to the caller.
"""
