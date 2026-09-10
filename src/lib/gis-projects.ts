/**
 * Project catalogue for the GIS check's "existing project" selector.
 *
 * NOTE: the backend has no projects endpoint -- the FastAPI service exposes prediction,
 * similarity, priority, and GIS boundary APIs, none of which own a project registry. This
 * list is therefore local frontend data, kept in one file so it can be replaced by a real
 * `GET /projects` call without touching the page.
 *
 * The GIS analysis itself is never mocked: whichever coordinates are selected here are
 * sent to the real spatial engine, and every result on screen comes back from it.
 *
 * Coordinates are chosen to sit in and around the backend's DEMO boundary dataset so the
 * feature can be exercised end to end; `SITE_UNKNOWN` deliberately has none, to exercise
 * the manual-entry path.
 */

export type GisProject = {
  id: string;
  name: string;
  sector: string;
  state: string;
  /** Null when the project record carries no surveyed location yet. */
  latitude: number | null;
  longitude: number | null;
};

export const GIS_PROJECTS: GisProject[] = [
  {
    id: "EFC-04",
    name: "Eastern Freight Corridor Expansion",
    sector: "Railways",
    state: "DEMO STATE",
    latitude: 21.2,
    longitude: 78.2,
  },
  {
    id: "NH-217",
    name: "NH-217 Bypass Realignment",
    sector: "Roads",
    state: "DEMO STATE",
    latitude: 20.585,
    longitude: 78.94,
  },
  {
    id: "TRN-88",
    name: "Northern Transmission Link",
    sector: "Power",
    state: "DEMO STATE",
    latitude: 21.38,
    longitude: 78.55,
  },
  {
    id: "UWS-12",
    name: "Lucknow Urban Water Supply Phase II",
    sector: "Water Resources",
    state: "Uttar Pradesh",
    latitude: 26.8467,
    longitude: 80.9462,
  },
  {
    id: "SITE-UNKNOWN",
    name: "Unsurveyed Industrial Corridor Site",
    sector: "Urban Development",
    state: "DEMO STATE",
    latitude: null,
    longitude: null,
  },
];

export const BUFFER_PRESETS = [
  { label: "500 m", meters: 500 },
  { label: "1 km", meters: 1000 },
  { label: "2 km", meters: 2000 },
  { label: "5 km", meters: 5000 },
] as const;
