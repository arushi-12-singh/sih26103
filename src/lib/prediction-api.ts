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
  project_name: string;
  sector: string;
  state: string;
  similarity_score: number;
  actual_delay_months: number;
  actual_cost_overrun_percentage: number;
  final_status: string;
  primary_delay_cause: string;
  intervention_taken: string;
  intervention_outcome: string;
};

export type HistoricalEvidence = {
  projects_analyzed: number;
  average_similarity: number;
  average_actual_delay_months: number;
  average_cost_overrun_percentage: number;
  projects_with_significant_delay: number;
  significant_delay_percentage: number;
  most_common_delay_cause: string;
};

export type SimilarityResponse = {
  similar_projects: HistoricalProjectMatch[];
  historical_evidence: HistoricalEvidence;
  historical_summary: string;
};

export async function findSimilarProjects(input: ProjectRiskInput, topK = 5): Promise<SimilarityResponse> {
  const response = await fetch(`${API_URL}/api/v1/similar-projects?top_k=${topK}`, {
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

// --- GIS & Environmental Boundary API ---

export type GISBufferInput = {
  project_id?: string;
  latitude: number;
  longitude: number;
  buffer_distance_km: number;
  zone_categories?: string[];
};

export type ZoneCollision = {
  zone_id: string;
  zone_name: string;
  zone_category: string;
  state: string;
  designation: string;
  clearance_type_required: string;
  distance_to_boundary_km: number;
  is_direct_intersection: boolean;
  intersection_area_sq_km: number;
  severity: "CRITICAL" | "HIGH" | "WARNING";
};

export type GISCollisionResponse = {
  has_collision: boolean;
  total_collisions: number;
  highest_severity: "NONE" | "WARNING" | "HIGH" | "CRITICAL";
  clearance_required: boolean;
  buffer_distance_km: number;
  project_coordinates: { latitude: number; longitude: number };
  collisions: ZoneCollision[];
  geojson_layers: GISGeoJSON;
  summary: string;
};

export type GISFeatureProperties = {
  name?: string;
  category?: string;
  state?: string;
  collision_severity?: string;
  clearance_type_required?: string;
  [key: string]: unknown;
};

export type GISGeoJSON = FeatureCollection<Geometry, GISFeatureProperties>;

export async function checkGisCollision(input: GISBufferInput): Promise<GISCollisionResponse> {
  const response = await fetch(`${API_URL}/api/v1/gis/check-collision`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => null);
    throw new Error(error?.detail?.[0]?.msg ?? error?.detail ?? `GIS collision check failed (${response.status})`);
  }
  return response.json() as Promise<GISCollisionResponse>;
}

export async function fetchProtectedZones(categories?: string[]): Promise<GISGeoJSON> {
  const params = categories?.length ? `?${categories.map(c => `category=${encodeURIComponent(c)}`).join("&")}` : "";
  const response = await fetch(`${API_URL}/api/v1/gis/protected-zones${params}`);
  if (!response.ok) {
    throw new Error(`Failed to fetch protected zones (${response.status})`);
  }
  return response.json();
}

// --- Project Document Management API ---

export type DocumentCategory =
  | "Detailed Project Report (DPR)"
  | "Environmental Clearance"
  | "Land Acquisition Record"
  | "Financial & Expenditure Report"
  | "Site Survey & Geotechnical"
  | "Contract & Tender Agreement"
  | "Other / Supporting Document";

export type DocumentMetadata = {
  document_id: string;
  project_id: string;
  filename: string;
  original_filename: string;
  category: DocumentCategory;
  description: string | null;
  file_size_bytes: number;
  mime_type: string;
  uploaded_at: string;
  uploader: string;
};

export type DocumentUploadResponse = {
  success: boolean;
  message: string;
  document: DocumentMetadata;
};

export type DocumentListResponse = {
  project_id: string;
  total_count: number;
  total_size_bytes: number;
  documents: DocumentMetadata[];
};

export async function uploadProjectDocument(
  projectId: string,
  file: File,
  category: DocumentCategory = "Other / Supporting Document",
  description?: string,
  uploader = "Ananya Sharma"
): Promise<DocumentUploadResponse> {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("category", category);
  if (description) formData.append("description", description);
  if (uploader) formData.append("uploader", uploader);

  const response = await fetch(`${API_URL}/api/v1/projects/${encodeURIComponent(projectId)}/documents`, {
    method: "POST",
    body: formData,
  });

  if (!response.ok) {
    const error = await response.json().catch(() => null);
    throw new Error(error?.detail?.[0]?.msg ?? error?.detail ?? `Document upload failed (${response.status})`);
  }

  return response.json() as Promise<DocumentUploadResponse>;
}

export async function fetchProjectDocuments(projectId: string): Promise<DocumentListResponse> {
  const response = await fetch(`${API_URL}/api/v1/projects/${encodeURIComponent(projectId)}/documents`);
  if (!response.ok) {
    const error = await response.json().catch(() => null);
    throw new Error(error?.detail?.[0]?.msg ?? error?.detail ?? `Failed to fetch documents (${response.status})`);
  }
  return response.json() as Promise<DocumentListResponse>;
}

export async function deleteProjectDocument(projectId: string, documentId: string): Promise<{ status: string; message: string }> {
  const response = await fetch(
    `${API_URL}/api/v1/projects/${encodeURIComponent(projectId)}/documents/${encodeURIComponent(documentId)}`,
    {
      method: "DELETE",
    }
  );

  if (!response.ok) {
    const error = await response.json().catch(() => null);
    throw new Error(error?.detail?.[0]?.msg ?? error?.detail ?? `Failed to delete document (${response.status})`);
  }

  return response.json();
}

export function getDocumentDownloadUrl(projectId: string, documentId: string): string {
  return `${API_URL}/api/v1/projects/${encodeURIComponent(projectId)}/documents/${encodeURIComponent(documentId)}/download`;
}
import type { FeatureCollection, Geometry } from "geojson";
