export type ProjectRiskInput = {
  sector: string;
  state: string;
  original_cost: number;
  revised_cost: number;
  planned_duration_months: number;
  project_age_months: number;
  physical_progress: number;
  financial_progress: number;
  milestones_total: number;
  milestones_delayed: number;
  land_acquisition_pending: boolean;
  clearance_pending: boolean;
  funding_issue: boolean;
  contractor_issue: boolean;
  previous_schedule_deviation: number;
};

export type ProjectRecord = ProjectRiskInput & {
  project_id: string;
};

export type ProjectListFilters = {
  search?: string;
  sector?: string;
  state?: string;
};

export type RiskFactor = {
  factor: string;
  impact: "increases_risk" | "reduces_risk";
  importance: number;
  description: string;
};

export type ProjectRiskResponse = {
  project_risk: {
    delay_probability: number;
    risk_percentage: number;
    risk_level: "LOW" | "MODERATE" | "HIGH" | "CRITICAL";
    model_confidence: "LOW" | "MEDIUM" | "HIGH";
    confidence_basis: string;
  };
  top_risk_factors: RiskFactor[];
  summary: string;
};

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

export async function predictProjectRisk(input: ProjectRiskInput): Promise<ProjectRiskResponse> {
  const response = await fetch(`${API_URL}/api/v1/predict-risk`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => null);
    throw new Error(error?.detail?.[0]?.msg ?? error?.detail ?? `Prediction failed (${response.status})`);
  }
  return response.json() as Promise<ProjectRiskResponse>;
}

// --- Historical project similarity ---

export type HistoricalProjectMatch = {
  project_id: string;
  similarity_percentage: number;
  sector: string;
  state: string;
  original_cost: number;
  actual_delay_months: number;
  actual_outcome: string;
  primary_delay_cause: string;
};

export type SimilarityEvidence = {
  similar_projects_count: number;
  delayed_projects_count: number;
  delayed_over_six_months_count: number;
  delay_rate: number;
  summary: string;
};

export type SimilarityResponse = {
  matches: HistoricalProjectMatch[];
  evidence: SimilarityEvidence;
};

export type ProjectIntelligenceResponse = {
  project_risk: ProjectRiskResponse["project_risk"];
  top_risk_factors: RiskFactor[];
  risk_summary: string;
  similar_projects: Array<{
    project_id: string;
    similarity_score: number;
    sector: string;
    state: string;
    actual_delay_months: number;
    actual_cost_overrun_percentage: number;
    primary_delay_cause: string;
  }>;
  historical_evidence: {
    projects_analyzed: number;
    significant_delay_percentage: number;
    average_actual_delay_months: number;
    most_common_delay_cause: string;
  };
  historical_summary: string;
};

async function readApiError(response: Response, fallback: string): Promise<Error> {
  const error = await response.json().catch(() => null);
  return new Error(error?.detail?.[0]?.msg ?? error?.detail ?? `${fallback} (${response.status})`);
}

export async function getProject(projectId: string): Promise<ProjectRecord> {
  const response = await fetch(`${API_URL}/api/v1/projects/${encodeURIComponent(projectId)}`);
  if (!response.ok) {
    throw await readApiError(response, "Project lookup failed");
  }
  return response.json() as Promise<ProjectRecord>;
}

export async function getProjects(filters: ProjectListFilters = {}): Promise<ProjectRecord[]> {
  const params = new URLSearchParams();
  if (filters.search) params.set("search", filters.search);
  if (filters.sector) params.set("sector", filters.sector);
  if (filters.state) params.set("state", filters.state);
  const query = params.toString();
  const response = await fetch(`${API_URL}/api/v1/projects${query ? `?${query}` : ""}`);
  if (!response.ok) {
    throw await readApiError(response, "Project list failed");
  }
  return response.json() as Promise<ProjectRecord[]>;
}

export async function getProjectIntelligence(
  projectId: string,
  input: ProjectRiskInput,
): Promise<ProjectIntelligenceResponse> {
  const response = await fetch(`${API_URL}/api/v1/project-intelligence`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...input, project_id: projectId }),
  });
  if (!response.ok) {
    throw await readApiError(response, "Project intelligence failed");
  }
  return response.json() as Promise<ProjectIntelligenceResponse>;
}

export async function findSimilarProjects(input: ProjectRiskInput): Promise<SimilarityResponse> {
  const response = await fetch(`${API_URL}/api/v1/similar-projects`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => null);
    throw new Error(error?.detail?.[0]?.msg ?? error?.detail ?? `Similarity search failed (${response.status})`);
  }
  return response.json() as Promise<SimilarityResponse>;
}

export type GISBufferInput = { project_id?: string; latitude: number; longitude: number; buffer_distance_km: number; zone_categories?: string[] };
export type ZoneCollision = { zone_id: string; zone_name: string; zone_category: string; state: string; designation: string; clearance_type_required: string; distance_to_boundary_km: number; is_direct_intersection: boolean; intersection_area_sq_km: number; severity: "CRITICAL" | "HIGH" | "WARNING" };
export type GISFeatureProperties = { name?: string; category?: string; state?: string; collision_severity?: string; clearance_type_required?: string; [key: string]: unknown };
export type GISGeoJSON = import("geojson").FeatureCollection<import("geojson").Geometry, GISFeatureProperties>;
export type GISCollisionResponse = { has_collision: boolean; total_collisions: number; highest_severity: "NONE" | "WARNING" | "HIGH" | "CRITICAL"; clearance_required: boolean; buffer_distance_km: number; project_coordinates: { latitude: number; longitude: number }; collisions: ZoneCollision[]; geojson_layers: GISGeoJSON; summary: string };

export async function checkGisCollision(input: GISBufferInput): Promise<GISCollisionResponse> {
  const response = await fetch(`${API_URL}/api/v1/gis/check-collision`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input) });
  if (!response.ok) throw new Error(`GIS collision check failed (${response.status})`);
  return response.json() as Promise<GISCollisionResponse>;
}

export type DocumentCategory = "Detailed Project Report (DPR)" | "Environmental Clearance" | "Land Acquisition Record" | "Financial & Expenditure Report" | "Site Survey & Geotechnical" | "Contract & Tender Agreement" | "Other / Supporting Document";
export type DocumentMetadata = { document_id: string; project_id: string; filename: string; original_filename: string; category: DocumentCategory; description: string | null; file_size_bytes: number; mime_type: string; uploaded_at: string; uploader: string };
export type DocumentUploadResponse = { success: boolean; message: string; document: DocumentMetadata };
export type DocumentListResponse = { project_id: string; total_count: number; total_size_bytes: number; documents: DocumentMetadata[] };

export async function fetchProjectDocuments(projectId: string): Promise<DocumentListResponse> {
  const response = await fetch(`${API_URL}/api/v1/projects/${encodeURIComponent(projectId)}/documents`);
  if (!response.ok) throw new Error(`Failed to fetch documents (${response.status})`);
  return response.json() as Promise<DocumentListResponse>;
}
export async function uploadProjectDocument(projectId: string, file: File, category: DocumentCategory = "Other / Supporting Document", description?: string, uploader = "Ananya Sharma"): Promise<DocumentUploadResponse> {
  const formData = new FormData(); formData.append("file", file); formData.append("category", category); if (description) formData.append("description", description); formData.append("uploader", uploader);
  const response = await fetch(`${API_URL}/api/v1/projects/${encodeURIComponent(projectId)}/documents`, { method: "POST", body: formData });
  if (!response.ok) throw new Error(`Document upload failed (${response.status})`);
  return response.json() as Promise<DocumentUploadResponse>;
}
export async function deleteProjectDocument(projectId: string, documentId: string): Promise<{ status: string; message: string }> {
  const response = await fetch(`${API_URL}/api/v1/projects/${encodeURIComponent(projectId)}/documents/${encodeURIComponent(documentId)}`, { method: "DELETE" });
  if (!response.ok) throw new Error(`Failed to delete document (${response.status})`);
  return response.json();
}
export function getDocumentDownloadUrl(projectId: string, documentId: string): string {
  return `${API_URL}/api/v1/projects/${encodeURIComponent(projectId)}/documents/${encodeURIComponent(documentId)}/download`;
}