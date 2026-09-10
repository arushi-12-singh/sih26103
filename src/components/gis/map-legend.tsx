"use client";

/**
 * Map palette, layer definitions, and the legend.
 *
 * Kept in its own module, free of any Leaflet import, so the page can render the legend
 * and own the layer-toggle state without pulling the map bundle into the server render
 * (the map itself is loaded client-only through next/dynamic).
 */

import { CATEGORY_LABELS, type BoundaryCategory } from "@/lib/gis-api";

/** One colour per category, muted to sit inside the app's existing palette. */
export const CATEGORY_COLORS: Record<BoundaryCategory, string> = {
  WILDLIFE_SANCTUARY: "#6b8f5e",
  NATIONAL_PARK: "#3f7a55",
  FOREST: "#7a8b46",
  ECO_SENSITIVE_ZONE: "#b08a3c",
  TIGER_RESERVE: "#b5623a",
  RAMSAR_WETLAND: "#4a7f96",
  OTHER_RESTRICTED_ZONE: "#7d7f88",
};

export const COLLISION_COLOR = "#c7543f";
export const BUFFER_COLOR = "#31463a";
export const PROJECT_COLOR = "#1c211f";

export type LayerKey = "project" | "buffer" | "boundaries" | "intersecting" | "collisions";
export type LayerVisibility = Record<LayerKey, boolean>;

export const DEFAULT_LAYERS: LayerVisibility = {
  project: true,
  buffer: true,
  boundaries: true,
  intersecting: true,
  collisions: true,
};

export const LAYER_OPTIONS: { key: LayerKey; label: string }[] = [
  { key: "project", label: "Project location" },
  { key: "buffer", label: "Analysis buffer" },
  { key: "boundaries", label: "All boundaries" },
  { key: "intersecting", label: "Detected boundaries" },
  { key: "collisions", label: "Collision areas" },
];

/**
 * Compact legend.
 *
 * `presentCategories` comes from the data actually on the map, and only adds the
 * catch-all "Other Restricted Zone" row when something on screen uses it -- a legend
 * entry for a colour nobody can see is noise.
 */
export function MapLegend({ presentCategories }: { presentCategories?: BoundaryCategory[] }) {
  const categories: BoundaryCategory[] = [
    "WILDLIFE_SANCTUARY",
    "NATIONAL_PARK",
    "FOREST",
    "ECO_SENSITIVE_ZONE",
    "TIGER_RESERVE",
    "RAMSAR_WETLAND",
  ];
  if (presentCategories?.includes("OTHER_RESTRICTED_ZONE")) categories.push("OTHER_RESTRICTED_ZONE");

  return (
    <ul className="gis-legend" aria-label="Map legend">
      <li>
        <i className="gis-legend-swatch project" aria-hidden />
        Project Location
      </li>
      <li>
        <i className="gis-legend-swatch buffer" aria-hidden />
        Analysis Buffer
      </li>
      {categories.map((category) => (
        <li key={category}>
          <i
            className="gis-legend-swatch"
            style={{ borderColor: CATEGORY_COLORS[category], background: `${CATEGORY_COLORS[category]}33` }}
            aria-hidden
          />
          {category === "RAMSAR_WETLAND" ? "Wetland" : CATEGORY_LABELS[category]}
        </li>
      ))}
      <li>
        <i className="gis-legend-swatch collision" aria-hidden />
        Collision Area
      </li>
    </ul>
  );
}
