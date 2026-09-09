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