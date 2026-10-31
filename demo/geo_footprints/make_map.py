"""
Spike: render the samples from fetch_samples.py onto one Leaflet map.

Writes a self-contained data/geo_samples/map.html with every
data/geo_samples/*.geojson embedded as a toggleable layer. Click a
feature to see its properties. Open the file in any browser; the only
network access it needs is Leaflet and the basemap tiles.

    uv run python spikes/geo_footprints/make_map.py
"""

# Imports

import json
from pathlib import Path


# Constants

SAMPLE_DIR = Path('data/geo_samples')

OUT_PATH = SAMPLE_DIR / 'map.html'

# Fallback colour per source; ShakeMap contours carry their own colour.
SOURCE_COLOURS = {
    'CEMS': '#1f6feb',
    'USGS': '#d4380d',
    'GDACS': '#7c3aed',
    'IBTrACS': '#0f766e',
    'PDC': '#b45309',
}

TEMPLATE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Footprint Samples</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  html, body, #map { height: 100%; margin: 0; }
  .leaflet-popup-content { max-height: 300px; overflow: auto; }
  .leaflet-popup-content table { border-collapse: collapse; font-size: 12px; }
  .leaflet-popup-content td { border-top: 1px solid #ddd; padding: 2px 6px;
                               vertical-align: top; }
  .leaflet-control-layers-overlays label { font-size: 12px; }
</style>
</head>
<body>
<div id="map"></div>
<script>
const SAMPLES = __SAMPLES__;
const COLOURS = __COLOURS__;

// OSM's own tile servers refuse requests without a Referer, which is
// what a page opened from disk sends, so use CARTO's OSM-based tiles.
const streets = L.tileLayer(
  'https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png',
  {maxZoom: 20, subdomains: 'abcd',
   attribution: '&copy; OpenStreetMap contributors &copy; CARTO'});
const imagery = L.tileLayer(
  'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
  {maxZoom: 19, attribution: 'Esri World Imagery'});
const map = L.map('map', {layers: [streets]});

function style(feature) {
  const p = feature.properties;
  const colour = p.color || COLOURS[p._source] || '#333';
  return {color: colour, weight: 2, fillColor: colour, fillOpacity: 0.3};
}

function popup(feature, layer) {
  const rows = Object.entries(feature.properties)
    .map(([k, v]) => `<tr><td><b>${k}</b></td><td>${
      typeof v === 'object' ? JSON.stringify(v) : v}</td></tr>`)
    .join('');
  layer.bindPopup(`<table>${rows}</table>`);
}

const overlays = {};
const bounds = L.latLngBounds([]);
const jumps = [];
for (const [name, collection] of Object.entries(SAMPLES)) {
  const first = collection.features[0];
  const label = first
    ? `${first.properties._source}: ${first.properties._layer} ` +
      `(${first.properties._note}) [${collection.features.length}]`
    : `${name} [empty]`;
  const layer = L.geoJSON(collection, {
    style: style,
    onEachFeature: popup,
    pointToLayer: (f, latlng) => L.circleMarker(latlng, {radius: 6}),
  }).addTo(map);
  overlays[label] = layer;
  if (layer.getBounds().isValid()) {
    bounds.extend(layer.getBounds());
    jumps.push([label, layer]);
  }
}

// Most footprints are a few km across and invisible at world zoom, so
// offer a list that zooms straight to each one.
const jump = L.control({position: 'topleft'});
jump.onAdd = () => {
  const select = L.DomUtil.create('select');
  select.style.maxWidth = '320px';
  select.innerHTML = '<option value="">Jump to sample...</option>' +
    jumps.map(([label], i) => `<option value="${i}">${label}</option>`).join('');
  L.DomEvent.disableClickPropagation(select);
  select.onchange = () => {
    if (select.value === '') return;
    const layer = jumps[select.value][1];
    if (!map.hasLayer(layer)) layer.addTo(map);
    map.fitBounds(layer.getBounds(), {maxZoom: 15});
  };
  return select;
};
jump.addTo(map);

L.control.layers({'Streets': streets, 'Satellite': imagery}, overlays,
                 {collapsed: false}).addTo(map);
L.control.scale().addTo(map);
map.fitBounds(bounds.isValid() ? bounds : [[-60, -180], [80, 180]]);
</script>
</body>
</html>
"""


# Main

def main() -> None:
    samples = {
        path.stem: json.loads(path.read_text())
        for path in sorted(SAMPLE_DIR.glob('*.geojson'))
    }
    if not samples:
        raise SystemExit(
            f'No samples in {SAMPLE_DIR}; run fetch_samples.py first.',
        )
    html = (
        TEMPLATE
        .replace('__SAMPLES__', json.dumps(samples))
        .replace('__COLOURS__', json.dumps(SOURCE_COLOURS))
    )
    OUT_PATH.write_text(html)
    print(f'Wrote {OUT_PATH} with {len(samples)} layers.')


if __name__ == '__main__':
    main()
