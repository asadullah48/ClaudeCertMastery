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

/* ---- Ask Zia companion panel ---- */

export interface ZiaCitation {
  slug: string;
  title: string | null;
  heading_path: string;
  url: string | null;
}

export interface ZiaExplain {
  ok: boolean;
  /** False means the panel hides itself: no mapping, tutor down, or corpus gap. */
  available: boolean;
  concept_tag: string | null;
  concept_label: string | null;
  matched_by: string | null;
  explanation: string;
  citations: ZiaCitation[];
  follow_up_question: string | null;
  detail: string;
}

export interface ZiaSession {
  ok: boolean;
  started_new_session: boolean;
  session_handle: string | null;
  detail: string;
}

export interface ZiaCheckAnswer {
  ok: boolean;
  recorded: boolean;
  detail: string;
}

export interface ZiaConcept {
  concept_tag: string;
  label: string;
  lesson_slug: string;
  lesson_title: string | null;
  lesson_url: string | null;
  confidence: number;
}

export interface ZiaConcepts {
  track_code: string;
  enabled: boolean;
  concepts: ZiaConcept[];
  /** Objectives with no lesson behind them, recorded rather than hidden. */
  unmapped: string[];
}
