"use client";

/**
 * GIS boundary map.
 *
 * Draws only what the backend returned. Every polygon on screen -- boundaries, the
 * analysis buffer, and the collision footprints -- arrives as GeoJSON from FastAPI; the
 * buffer is the real metre circle the engine tested, projected back to WGS84 by the
 * engine itself, and each collision area is the exact overlap it measured
 * `intersection_area_sqm` from. Nothing here re-projects, re-buffers, or re-intersects,
 * and no decorative geometry is synthesised: a shape drawn from client-side maths could
 * disagree with the verdict shown beside it.
 *
 * Leaflet was chosen because no map library existed in the project and Mapbox was not
 * configured; it needs no access token and renders GeoJSON natively. It touches `window`
 * on import, so the page loads this through next/dynamic with ssr:false.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { MapContainer, TileLayer, GeoJSON, CircleMarker, Tooltip, useMap } from "react-leaflet";
import type { Layer, Map as LeafletMap, PathOptions } from "leaflet";
import type { Feature, FeatureCollection, Geometry } from "geojson";
import { Layers, Maximize2, RotateCcw } from "lucide-react";

import { formatArea, formatDistance, type BoundaryCategory, type GeoJsonFeatureCollection } from "@/lib/gis-api";
import {
  BUFFER_COLOR,
  CATEGORY_COLORS,
  COLLISION_COLOR,
  DEFAULT_LAYERS,
  LAYER_OPTIONS,
  PROJECT_COLOR,
  type LayerKey,
  type LayerVisibility,
} from "@/components/gis/map-legend";

import "leaflet/dist/leaflet.css";

const DEFAULT_CENTER: [number, number] = [22.5, 79.0];
const DEFAULT_ZOOM = 4;
const POINT_ZOOM = 12;

export type BoundaryMapProps = {
  /** The analysis FeatureCollection from POST /check-collision, or null before a run. */
  collection: GeoJsonFeatureCollection | null;
  /** Every stored boundary, from GET /boundaries -- the context layer. */
  contextCollection: GeoJsonFeatureCollection | null;
  latitude: number | null;
  longitude: number | null;
  /** Boundary id to emphasise, driven by hovering a row in the results table. */
  highlightedId?: string | null;
  layers?: LayerVisibility;
  onLayersChange?: (layers: LayerVisibility) => void;
  /** True when at least one drawn boundary is generated demo data. */
  containsDemoData?: boolean;
};

type Props = Record<string, unknown>;

function categoryOf(properties: Props | undefined): BoundaryCategory | null {
  const value = properties?.category;
  return typeof value === "string" && value in CATEGORY_COLORS ? (value as BoundaryCategory) : null;
}

function colorOf(properties: Props | undefined): string {
  const category = categoryOf(properties);
  return category ? CATEGORY_COLORS[category] : CATEGORY_COLORS.OTHER_RESTRICTED_ZONE;
}

/** Boundary names and sources are data, so they are escaped before reaching popup HTML. */
function escapeHtml(value: unknown): string {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function popupHtml(properties: Props): string {
  const rows: [string, string][] = [
    ["Category", escapeHtml(properties.category_label ?? properties.category ?? "--")],
    ["State", escapeHtml(properties.state ?? "--")],
    ["District", escapeHtml(properties.district ?? "Not specified")],
    ["Source", escapeHtml(properties.source ?? "--")],
    ["Last updated", escapeHtml(properties.last_updated ?? "--")],
  ];

  if (typeof properties.collision_type === "string") {
    rows.push(["Result", escapeHtml(String(properties.collision_type).replace(/_/g, " "))]);
  }
  if (typeof properties.distance_meters === "number") {
    rows.push(["Distance", escapeHtml(formatDistance(properties.distance_meters))]);
  }
  if (typeof properties.intersection_area_sqm === "number" && properties.intersection_area_sqm > 0) {
    rows.push(["Overlap area", escapeHtml(formatArea(properties.intersection_area_sqm))]);
  }

  const sourceUrl = typeof properties.source_url === "string" ? properties.source_url : null;
  const demoBanner = properties.is_demo
    ? `<p class="gis-popup-demo">${escapeHtml(properties.data_notice ?? "DEMO DATA - NOT OFFICIAL BOUNDARIES")}</p>`
    : "";

  return [
    `<div class="gis-popup">`,
    `<h4>${escapeHtml(properties.name ?? "Boundary")}</h4>`,
    demoBanner,
    `<dl>`,
    ...rows.map(([label, value]) => `<div><dt>${label}</dt><dd>${value}</dd></div>`),
    `</dl>`,
    sourceUrl
      ? `<a href="${escapeHtml(sourceUrl)}" target="_blank" rel="noreferrer noopener">View source dataset</a>`
      : "",
    `</div>`,
  ].join("");
}

/** Imperative map controls, mounted inside MapContainer so they can reach the instance. */
function MapControls({
  onReady,
}: {
  onReady: (map: LeafletMap) => void;
}) {
  const map = useMap();
  useEffect(() => {
    onReady(map);
  }, [map, onReady]);
  return null;
}

/**
 * Fits the viewport to the latest analysis.
 *
 * Keyed on a signature of the analysis rather than on object identity: React re-renders
 * on every hover, and re-fitting then would yank a user who has panned or zoomed in to
 * inspect something back to the overview.
 */
function AutoFit({
  collection,
  latitude,
  longitude,
}: {
  collection: GeoJsonFeatureCollection | null;
  latitude: number | null;
  longitude: number | null;
}) {
  const map = useMap();
  const previous = useRef<string | null>(null);

  useEffect(() => {
    const signature = `${latitude},${longitude},${collection?.features.length ?? 0},${
      collection?.features.find((f) => f.properties?.role === "analysis_buffer")?.properties
        ?.buffer_meters ?? ""
    }`;
    if (previous.current === signature) return;
    previous.current = signature;

    if (collection && collection.features.length > 0) {
      void import("leaflet").then((L) => {
        const bounds = L.geoJSON(collection as unknown as FeatureCollection).getBounds();
        if (bounds.isValid()) map.fitBounds(bounds, { padding: [40, 40], maxZoom: 14 });
      });
    } else if (latitude !== null && longitude !== null) {
      map.setView([latitude, longitude], POINT_ZOOM);
    }
  }, [collection, latitude, longitude, map]);

  return null;
}

function subset(
  collection: GeoJsonFeatureCollection | null,
  role: string,
): FeatureCollection | null {
  if (!collection) return null;
  const features = collection.features.filter((feature) => feature.properties?.role === role);
  return features.length
    ? ({ type: "FeatureCollection", features } as unknown as FeatureCollection)
    : null;
}

export default function BoundaryMap({
  collection,
  contextCollection,
  latitude,
  longitude,
  highlightedId,
  layers = DEFAULT_LAYERS,
  onLayersChange,
  containsDemoData = false,
}: BoundaryMapProps) {
  const mapRef = useRef<LeafletMap | null>(null);
  const [showLayerPanel, setShowLayerPanel] = useState(false);

  const handleReady = useCallback((map: LeafletMap) => {
    mapRef.current = map;
  }, []);

  const intersecting = useMemo(() => subset(collection, "boundary"), [collection]);
  const buffer = useMemo(() => subset(collection, "analysis_buffer"), [collection]);
  const collisions = useMemo(() => subset(collection, "collision_area"), [collection]);

  /** Ids already drawn by the analysis layer, so the context layer does not double-draw. */
  const analysedIds = useMemo(() => {
    const ids = new Set<string>();
    intersecting?.features.forEach((feature) => {
      const id = (feature.properties as Props | undefined)?.id;
      if (typeof id === "string") ids.add(id);
    });
    return ids;
  }, [intersecting]);

  const context = useMemo<FeatureCollection | null>(() => {
    if (!contextCollection) return null;
    const features = contextCollection.features.filter((feature) => {
      const id = feature.properties?.id;
      return typeof id !== "string" || !analysedIds.has(id);
    });
    return features.length
      ? ({ type: "FeatureCollection", features } as unknown as FeatureCollection)
      : null;
  }, [contextCollection, analysedIds]);

  const contextStyle = (feature?: Feature<Geometry, Props>): PathOptions => ({
    color: colorOf(feature?.properties),
    weight: 1,
    opacity: 0.65,
    fillColor: colorOf(feature?.properties),
    fillOpacity: 0.08,
  });

  const intersectingStyle = (feature?: Feature<Geometry, Props>): PathOptions => {
    const isHighlighted = highlightedId != null && feature?.properties?.id === highlightedId;
    const nearby = feature?.properties?.collision_type === "NEARBY";
    return {
      color: colorOf(feature?.properties),
      weight: isHighlighted ? 3.5 : 2,
      opacity: 1,
      fillColor: colorOf(feature?.properties),
      fillOpacity: isHighlighted ? 0.34 : 0.2,
      // Dashed for NEARBY: it is a proximity advisory, not an intersection.
      dashArray: nearby ? "5 4" : undefined,
    };
  };

  const collisionStyle: PathOptions = {
    color: COLLISION_COLOR,
    weight: 1.5,
    fillColor: COLLISION_COLOR,
    fillOpacity: 0.5,
  };

  const bufferStyle: PathOptions = {
    color: BUFFER_COLOR,
    weight: 1.5,
    dashArray: "5 4",
    fillColor: BUFFER_COLOR,
    fillOpacity: 0.05,
  };

  const bindBoundary = (feature: Feature<Geometry, Props>, layer: Layer) => {
    const properties = feature.properties ?? {};
    layer.bindPopup(popupHtml(properties), { className: "gis-popup-wrap", maxWidth: 290 });
    layer.bindTooltip(escapeHtml(properties.name ?? "Boundary"), {
      className: "gis-map-tooltip",
      sticky: true,
    });
  };

  const bindCollision = (feature: Feature<Geometry, Props>, layer: Layer) => {
    const properties = feature.properties ?? {};
    const overlap =
      typeof properties.buffer_overlap_percentage === "number"
        ? `${properties.buffer_overlap_percentage.toFixed(1)}% of buffer`
        : "";
    layer.bindTooltip(
      `<strong>Collision area</strong><br/>${escapeHtml(properties.name)}<br/>${escapeHtml(
        formatArea(Number(properties.intersection_area_sqm ?? 0)),
      )}<br/>${escapeHtml(overlap)}`,
      { className: "gis-map-tooltip", sticky: true },
    );
  };

  const fitToResults = () => {
    const map = mapRef.current;
    if (!map) return;
    const target = collection ?? contextCollection;
    if (target && target.features.length > 0) {
      void import("leaflet").then((L) => {
        const bounds = L.geoJSON(target as unknown as FeatureCollection).getBounds();
        if (bounds.isValid()) map.fitBounds(bounds, { padding: [40, 40], maxZoom: 14 });
      });
    } else if (latitude !== null && longitude !== null) {
      map.setView([latitude, longitude], POINT_ZOOM);
    }
  };

  const resetView = () => {
    const map = mapRef.current;
    if (!map) return;
    if (latitude !== null && longitude !== null) map.setView([latitude, longitude], POINT_ZOOM);
    else map.setView(DEFAULT_CENTER, DEFAULT_ZOOM);
  };

  const toggleLayer = (key: LayerKey) => {
    onLayersChange?.({ ...layers, [key]: !layers[key] });
  };

  return (
    <div className="gis-map-shell">
      <div className="gis-map-controls">
        <button type="button" onClick={fitToResults} title="Fit to results" aria-label="Fit map to results">
          <Maximize2 size={14} />
        </button>
        <button type="button" onClick={resetView} title="Reset view" aria-label="Reset map view">
          <RotateCcw size={14} />
        </button>
        <button
          type="button"
          onClick={() => setShowLayerPanel((open) => !open)}
          title="Layers"
          aria-label="Toggle layer visibility panel"
          aria-expanded={showLayerPanel}
          className={showLayerPanel ? "active" : undefined}
        >
          <Layers size={14} />
        </button>
      </div>

      {showLayerPanel && (
        <div className="gis-layer-panel" role="group" aria-label="Layer visibility">
          {LAYER_OPTIONS.map((option) => (
            <label key={option.key}>
              <input
                type="checkbox"
                checked={layers[option.key]}
                onChange={() => toggleLayer(option.key)}
              />
              <span>{option.label}</span>
            </label>
          ))}
        </div>
      )}

      {containsDemoData && <p className="gis-map-demo-banner">DEMO DATA — NOT OFFICIAL BOUNDARIES</p>}

      <MapContainer
        center={latitude !== null && longitude !== null ? [latitude, longitude] : DEFAULT_CENTER}
        zoom={latitude !== null ? POINT_ZOOM : DEFAULT_ZOOM}
        scrollWheelZoom
        className="gis-leaflet"
      >
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
          maxZoom={19}
        />

        {/* Layer 3 -- every stored boundary, as context around the analysis. */}
        {layers.boundaries && context && (
          <GeoJSON
            key={`context-${context.features.length}`}
            data={context}
            style={contextStyle}
            onEachFeature={bindBoundary}
          />
        )}

        {/* Layer 2 -- the buffer the engine actually tested. */}
        {layers.buffer && buffer && (
          <GeoJSON key={`buffer-${JSON.stringify(buffer.features[0]?.properties)}`} data={buffer} style={bufferStyle} />
        )}

        {/* Layer 4 -- boundaries the analysis flagged, drawn over the context layer. */}
        {layers.intersecting && intersecting && (
          <GeoJSON
            // Re-key on the highlight: Leaflet caches per-layer style and will not re-run
            // the style function otherwise.
            key={`intersecting-${intersecting.features.length}-${highlightedId ?? ""}`}
            data={intersecting}
            style={intersectingStyle}
            onEachFeature={bindBoundary}
          />
        )}

        {/* Layer 5 -- the exact overlap footprints, computed by the engine. */}
        {layers.collisions && collisions && (
          <GeoJSON
            key={`collisions-${collisions.features.length}`}
            data={collisions}
            style={collisionStyle}
            onEachFeature={bindCollision}
          />
        )}

        {/* Layer 1 -- the project location. */}
        {layers.project && latitude !== null && longitude !== null && (
          <CircleMarker
            center={[latitude, longitude]}
            radius={6}
            pathOptions={{ color: PROJECT_COLOR, weight: 2, fillColor: "#f8f9f7", fillOpacity: 1 }}
          >
            <Tooltip direction="top" offset={[0, -8]} className="gis-map-tooltip">
              Project location
              <br />
              {latitude.toFixed(5)}, {longitude.toFixed(5)}
            </Tooltip>
          </CircleMarker>
        )}

        <AutoFit collection={collection} latitude={latitude} longitude={longitude} />
        <MapControls onReady={handleReady} />
      </MapContainer>
    </div>
  );
}
