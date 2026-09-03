/** Shapes returned by the FastAPI backend. Mirrors app/schemas on the Python side. */

export interface Domain {
  id: number;
  code: string;
  name: string;
  description: string;
  weight_bps: number;
  position: number;
}

export interface Track {
  id: number;
  code: string;
  name: string;
  description: string;
  item_count: number;
  duration_minutes: number;
  pass_scaled_score: number;
  price_usd: number;
  validity_months: number;
  /** False for tracks whose blueprint exists but whose question bank does not yet. */
  is_seeded: boolean;
  question_count: number;
  domains: Domain[];
}

export interface BlueprintDomain {
  code: string;
  name: string;
  weight_pct: number;
  items_at_full_length: number;
  questions_available: number;
}

export interface Blueprint {
  track_code: string;
  item_count: number;
  total_weight_bps: number;
  domains: BlueprintDomain[];
}

export interface Health {
  status: string;
  version: string;
  ai_explanations_enabled: boolean;
}
