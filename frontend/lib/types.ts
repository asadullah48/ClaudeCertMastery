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

/* ---- Exam runner ---- */

export interface ExamOption {
  id: number;
  label: string;
  text: string;
  position: number;
}

export interface ExamQuestion {
  id: number;
  external_id: string;
  stem: string;
  /** "mcq" accepts exactly one option; "mr" accepts a set. */
  question_type: "mcq" | "mr";
  difficulty: number;
  domain_code: string;
  /** Never carries is_correct: the answer key does not leave the server. */
  options: ExamOption[];
}

export interface ExamGenerated {
  attempt_id: number;
  track_code: string;
  seed: number;
  item_count: number;
  duration_minutes: number;
  per_domain: Record<string, number>;
  /** Set when a domain held too few questions and its quota was redistributed. */
  composition_warning: string | null;
  questions: ExamQuestion[];
}

export interface SubmitAnswer {
  question_id: number;
  selected_option_ids: number[];
  time_spent_seconds: number | null;
  flagged_for_review: boolean;
}

export interface DomainScore {
  domain_code: string;
  domain_name: string;
  correct: number;
  total: number;
  percentage: number;
  mastery_band: string;
}

export interface ItemResult {
  question_id: number;
  external_id: string;
  domain_code: string;
  is_correct: boolean;
  partial_credit: number;
  selected_option_ids: number[];
  correct_option_ids: number[];
  /** The authored explanation. Always present, with or without an API key. */
  explanation: string;
}

export interface ExamResult {
  attempt_id: number;
  track_code: string;
  raw_correct: number;
  raw_total: number;
  raw_percentage: number;
  scaled_score: number;
  pass_scaled_score: number;
  passed: boolean;
  domain_scores: DomainScore[];
  items: ItemResult[];
  composition_warning: string | null;
}

/* ---- AI explanations ---- */

export interface Explanation {
  question_id: number;
  external_id: string;
  domain_code: string;
  /** "ai" when Claude wrote it, "static" when the authored fallback was served. */
  source: "ai" | "static";
  reused: boolean;
  why_correct: string;
  why_your_answer_wrong: string;
  key_concept: string;
  blueprint_link: string;
  study_tip: string;
  static_explanation: string;
  detail: string;
}

export interface ExplanationResponse {
  attempt_id: number;
  ai_enabled: boolean;
  generated: number;
  reused: number;
  fell_back: number;
  explanations: Explanation[];
}
