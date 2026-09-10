/**
 * Typed client for the GIS Environmental Boundary Check API.
 *
 * Every call reaches the real FastAPI service (via the same-origin proxy at
 * /api/gis/*, which attaches the server-held bearer token). Nothing here is mocked or
 * stubbed, and no response value is synthesised on the client -- if the backend is down
 * or unauthorised, that surfaces as a typed error rather than as placeholder data.
 *
 * Failures are modelled as distinct classes so the UI can render the right state
 * instead of collapsing everything into one "something went wrong": a 422 is the user's
 * input to fix, a 401/403 is an operator configuration problem, and an unreachable
 * backend is neither.
 */

/** Mirrors BoundaryCategory in backend/app/schemas/boundary.py. */
export const BOUNDARY_CATEGORIES = [
  "WILDLIFE_SANCTUARY",
  "NATIONAL_PARK",
  "FOREST",
  "ECO_SENSITIVE_ZONE",
  "TIGER_RESERVE",
  "RAMSAR_WETLAND",
  "OTHER_RESTRICTED_ZONE",
] as const;

export type BoundaryCategory = (typeof BOUNDARY_CATEGORIES)[number];

export const CATEGORY_LABELS: Record<BoundaryCategory, string> = {
  WILDLIFE_SANCTUARY: "Wildlife Sanctuary",
  NATIONAL_PARK: "National Park",
  FOREST: "Forest",
  ECO_SENSITIVE_ZONE: "Eco-Sensitive Zone",
  TIGER_RESERVE: "Tiger Reserve",
  RAMSAR_WETLAND: "Ramsar Wetland",
  OTHER_RESTRICTED_ZONE: "Other Restricted Zone",
};

export type CollisionType = "DIRECT_COLLISION" | "BUFFER_COLLISION" | "NEARBY" | "CLEAR";
export type Severity = "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";

export type GeoJsonGeometry = {
  type: "Polygon" | "MultiPolygon" | "Point";
  coordinates: unknown;
};

export type GeoJsonFeature = {
  type: "Feature";
  id?: string;
  geometry: GeoJsonGeometry | null;
  properties: Record<string, unknown> & { role?: "project_location" | "analysis_buffer" | "boundary" };
};

export type GeoJsonFeatureCollection = {
  type: "FeatureCollection";
  features: GeoJsonFeature[];
  notice?: string | null;
};

export type ProjectLocation = { latitude: number; longitude: number };

export type CollisionDetail = {
  boundary_id: string;
  boundary_name: string;
  category: BoundaryCategory;
  category_label: string;
  collision_type: CollisionType;
  severity: Severity;
  distance_meters: number;
  intersection_area_sqm: number;
  buffer_overlap_percentage: number;
  clearance_required: boolean;
  is_demo: boolean;
  state: string;
  district: string | null;
  geometry: GeoJsonGeometry | null;
};

export type ClearanceFlag = {
  boundary_id: string;
  boundary_name: string;
  category: BoundaryCategory;
  collision_type: CollisionType;
  severity: Severity;
  distance_meters: number;
  reason: string;
};

export type CollisionCheckResponse = {
  project_id: string | null;
  project_location: ProjectLocation;
  buffer_meters: number;
  proximity_threshold_meters: number;
  overall_status: CollisionType;
  overall_severity: Severity | null;
  clearance_required: boolean;
  boundaries_checked: number;
  candidates_examined: number;
  collision_count: number;
  collisions: CollisionDetail[];
  clearance_flags: ClearanceFlag[];
  geojson: GeoJsonFeatureCollection;
  projected_crs: string;
  contains_demo_data: boolean;
  notice: string | null;
  summary: string;
  analyzed_at: string;
};

export type CollisionCheckInput = {
  project_id?: string | null;
  latitude: number;
  longitude: number;
  buffer_meters: number;
  categories?: BoundaryCategory[] | null;
  include_demo?: boolean;
  include_geometry?: boolean;
};

export type NearbyBoundary = {
  boundary_id: string;
  name: string;
  category: BoundaryCategory;
  category_label: string;
  state: string;
  district: string | null;
  distance_meters: number;
  contains_point: boolean;
  severity: Severity;
  clearance_required: boolean;
  is_demo: boolean;
  geometry: GeoJsonGeometry | null;
};

export type NearbyBoundariesResponse = {
  project_location: ProjectLocation;
  radius_meters: number;
  categories: BoundaryCategory[] | null;
  count: number;
  boundaries: NearbyBoundary[];
  geojson: GeoJsonFeatureCollection;
  boundaries_checked: number;
  contains_demo_data: boolean;
  notice: string | null;
};

export type BoundarySummary = {
  id: string;
  name: string;
  category: BoundaryCategory;
  state: string;
  district: string | null;
  source: string;
  source_url: string | null;
  last_updated: string;
  area_sqkm: number;
  bbox: [number, number, number, number];
  is_demo: boolean;
};

export type BoundaryListResponse = {
  count: number;
  total: number;
  limit: number | null;
  offset: number;
  filters: Record<string, unknown>;
  boundaries: BoundarySummary[];
  geojson: GeoJsonFeatureCollection | null;
  contains_demo_data: boolean;
  notice: string | null;
};

export type BoundaryDetailResponse = {
  boundary: BoundarySummary;
  geometry: GeoJsonGeometry;
  feature: GeoJsonFeature;
  metadata: Record<string, unknown>;
  is_demo: boolean;
  notice: string | null;
};

export type IntersectingBoundary = Omit<CollisionDetail, "state" | "district" | "geometry">;

export type AssessmentRecord = {
  id: string;
  project_id: string;
  location: ProjectLocation;
  buffer_meters: number;
  created_at: string;
  created_by: string;
  created_by_name: string;
  overall_status: CollisionType;
  overall_severity: Severity | null;
  clearance_required: boolean;
  boundaries_checked: number;
  intersecting_boundaries: IntersectingBoundary[];
  clearance_flags: ClearanceFlag[];
  notes: string | null;
};

export type AssessmentHistoryResponse = {
  project_id: string;
  count: number;
  total: number;
  limit: number | null;
  offset: number;
  assessments: AssessmentRecord[];
};

// ---------------------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------------------

export class GisApiError extends Error {
  readonly status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "GisApiError";
    this.status = status;
  }
}

/** 422 -- the submitted coordinates, buffer, or filters are not acceptable. */
export class GisValidationError extends GisApiError {
  readonly fields: string[];
  constructor(message: string, fields: string[]) {
    super(message, 422);
    this.name = "GisValidationError";
    this.fields = fields;
  }
}

/** 401 / 403 / 503-unconfigured -- an operator problem, not something the user typed. */
export class GisAuthError extends GisApiError {
  constructor(message: string, status: number) {
    super(message, status);
    this.name = "GisAuthError";
  }
}

/** 404 -- the boundary or project history does not exist. */
export class GisNotFoundError extends GisApiError {
  constructor(message: string) {
    super(message, 404);
    this.name = "GisNotFoundError";
  }
}

/** 5xx from the service itself. */
export class GisServerError extends GisApiError {
  constructor(message: string, status: number) {
    super(message, status);
    this.name = "GisServerError";
  }
}

/** The request never got an HTTP answer: offline, DNS, or the proxy could not connect. */
export class GisNetworkError extends GisApiError {
  constructor(message: string) {
    super(message, 0);
    this.name = "GisNetworkError";
  }
}

type FastApiValidationItem = { loc?: (string | number)[]; msg?: string };

/**
 * Turn a FastAPI error body into one readable sentence.
 *
 * FastAPI reports request-validation failures as an array of {loc, msg} objects and
 * everything else as a plain `detail` string, so both shapes have to be handled or the
 * user sees "[object Object]".
 */
function describeDetail(detail: unknown): { message: string; fields: string[] } {
  if (typeof detail === "string") return { message: detail, fields: [] };

  if (Array.isArray(detail)) {
    const fields: string[] = [];
    const parts = (detail as FastApiValidationItem[]).map((item) => {
      const path = (item.loc ?? []).filter((part) => part !== "body" && part !== "query");
      const field = path.join(".");
      if (field) fields.push(field);
      return field ? `${field}: ${item.msg ?? "invalid"}` : (item.msg ?? "invalid");
    });
    return { message: parts.join("; "), fields };
  }

  return { message: "The GIS service rejected the request.", fields: [] };
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api/gis/${path}`, {
      ...init,
      headers: { Accept: "application/json", ...(init?.headers ?? {}) },
    });
  } catch (error) {
    throw new GisNetworkError(
      `Could not reach the GIS service. Check your connection and that the backend is running. (${
        error instanceof Error ? error.message : "network error"
      })`,
    );
  }

  if (response.status === 204) return undefined as T;

  const payload = await response.json().catch(() => null);

  if (response.ok) return payload as T;

  const { message, fields } = describeDetail(payload?.detail);

  if (response.status === 422) throw new GisValidationError(message, fields);
  if (response.status === 401 || response.status === 403) throw new GisAuthError(message, response.status);
  if (response.status === 404) throw new GisNotFoundError(message);
  if (response.status === 503 && /authentication/i.test(message)) throw new GisAuthError(message, 503);
  // The proxy emits 502 for exactly one reason: it could not reach FastAPI at all. That is
  // a connectivity problem, not a fault in the service, so it belongs in the network state
  // alongside a failed fetch -- the user's next step is "start the backend", not "retry later".
  if (response.status === 502) throw new GisNetworkError(message);
  if (response.status >= 500) throw new GisServerError(message, response.status);
  throw new GisApiError(message, response.status);
}

function queryString(params: Record<string, string | number | boolean | string[] | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined || value === "") continue;
    // Repeat the key for list filters -- the shape FastAPI expects for `list[...]` query params.
    if (Array.isArray(value)) value.forEach((item) => search.append(key, item));
    else search.append(key, String(value));
  }
  const encoded = search.toString();
  return encoded ? `?${encoded}` : "";
}

// ---------------------------------------------------------------------------------
// API surface
// ---------------------------------------------------------------------------------

export const gisApi = {
  /** POST /api/v1/gis/check-collision */
  checkCollision(input: CollisionCheckInput): Promise<CollisionCheckResponse> {
    return request<CollisionCheckResponse>("check-collision", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        project_id: input.project_id ?? null,
        latitude: input.latitude,
        longitude: input.longitude,
        buffer_meters: input.buffer_meters,
        categories: input.categories?.length ? input.categories : null,
        include_demo: input.include_demo ?? true,
        include_geometry: input.include_geometry ?? true,
      }),
    });
  },

  /** GET /api/v1/gis/nearby-boundaries */
  getNearbyBoundaries(params: {
    latitude: number;
    longitude: number;
    radius_meters: number;
    category?: BoundaryCategory[] | null;
    include_geometry?: boolean;
  }): Promise<NearbyBoundariesResponse> {
    return request<NearbyBoundariesResponse>(
      `nearby-boundaries${queryString({
        latitude: params.latitude,
        longitude: params.longitude,
        radius_meters: params.radius_meters,
        category: params.category ?? null,
        include_geometry: params.include_geometry ?? false,
      })}`,
    );
  },

  /** GET /api/v1/gis/boundaries */
  getBoundaries(params?: {
    category?: BoundaryCategory[] | null;
    state?: string | null;
    district?: string | null;
    search?: string | null;
    include_geometry?: boolean;
    limit?: number | null;
    offset?: number;
  }): Promise<BoundaryListResponse> {
    return request<BoundaryListResponse>(
      `boundaries${queryString({
        category: params?.category ?? null,
        state: params?.state ?? null,
        district: params?.district ?? null,
        search: params?.search ?? null,
        include_geometry: params?.include_geometry ?? false,
        limit: params?.limit ?? null,
        offset: params?.offset ?? 0,
      })}`,
    );
  },

  /** GET /api/v1/gis/boundaries/{id} */
  getBoundary(boundaryId: string): Promise<BoundaryDetailResponse> {
    return request<BoundaryDetailResponse>(`boundaries/${encodeURIComponent(boundaryId)}`);
  },

  /** GET /api/v1/gis/assessments/{project_id} -- 404 when the project has no history. */
  getAssessmentHistory(projectId: string, limit?: number): Promise<AssessmentHistoryResponse> {
    return request<AssessmentHistoryResponse>(
      `assessments/${encodeURIComponent(projectId)}${queryString({ limit: limit ?? null })}`,
    );
  },

  /** POST /api/v1/gis/assessments -- runs the engine and stores the outcome. */
  createAssessment(input: {
    project_id: string;
    latitude: number;
    longitude: number;
    buffer_meters: number;
    categories?: BoundaryCategory[] | null;
    notes?: string | null;
  }): Promise<AssessmentRecord> {
    return request<AssessmentRecord>("assessments", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        project_id: input.project_id,
        latitude: input.latitude,
        longitude: input.longitude,
        buffer_meters: input.buffer_meters,
        categories: input.categories?.length ? input.categories : null,
        notes: input.notes ?? null,
      }),
    });
  },
};

// ---------------------------------------------------------------------------------
// Display helpers -- formatting only; no analysis or classification happens client-side
// ---------------------------------------------------------------------------------

export const STATUS_LABELS: Record<CollisionType, string> = {
  DIRECT_COLLISION: "Direct collision",
  BUFFER_COLLISION: "Buffer collision",
  NEARBY: "Nearby boundary",
  CLEAR: "Clear",
};

export const STATUS_HEADLINES: Record<CollisionType, string> = {
  DIRECT_COLLISION: "Project location falls inside a restricted boundary",
  BUFFER_COLLISION: "Potential spatial conflict detected",
  NEARBY: "Restricted boundary close to the project location",
  CLEAR: "No spatial conflict detected",
};

/** Maps a severity onto the design system's existing pill classes. */
export const SEVERITY_CLASS: Record<Severity, string> = {
  CRITICAL: "critical",
  HIGH: "high",
  MEDIUM: "watch",
  LOW: "stable",
};

export function formatDistance(meters: number): string {
  if (meters === 0) return "Inside";
  if (meters < 1000) return `${Math.round(meters).toLocaleString()} m`;
  return `${(meters / 1000).toFixed(meters < 10_000 ? 2 : 1)} km`;
}

export function formatBuffer(meters: number): string {
  return meters >= 1000 ? `${(meters / 1000).toString().replace(/\.0$/, "")} km` : `${meters} m`;
}

export function formatArea(squareMeters: number): string {
  if (squareMeters <= 0) return "--";
  if (squareMeters < 1_000_000) return `${Math.round(squareMeters).toLocaleString()} m²`;
  return `${(squareMeters / 1_000_000).toFixed(2)} km²`;
}
