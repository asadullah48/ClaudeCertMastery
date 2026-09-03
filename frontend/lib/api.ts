import type {
  Blueprint,
  Health,
  Track,
  ZiaCheckAnswer,
  ZiaConcepts,
  ZiaExplain,
  ZiaSession,
} from "./types";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    // Track and blueprint data changes only when the bank is reseeded, but a stale
    // cache during development is more confusing than an extra request.
    cache: "no-store",
  });
  if (!res.ok) {
    throw new ApiError(`GET ${path} failed`, res.status);
  }
  return res.json() as Promise<T>;
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    throw new ApiError(`POST ${path} failed`, res.status);
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () => get<Health>("/health"),
  listTracks: () => get<Track[]>("/tracks"),
  getTrack: (code: string) => get<Track>(`/tracks/${code}`),
  getBlueprint: (code: string) => get<Blueprint>(`/tracks/${code}/blueprint`),

  // Ask Zia. These never throw for an unavailable tutor -- the backend returns
  // 200 with available:false so the panel can simply hide itself.
  ziaConcepts: (trackCode: string) =>
    get<ZiaConcepts>(`/api/zia/concepts?track_code=${encodeURIComponent(trackCode)}`),
  ziaSession: (goal?: string) => post<ZiaSession>("/api/zia/session", { goal }),
  ziaExplainByConcept: (trackCode: string, conceptTag: string) =>
    get<ZiaExplain>(
      `/api/zia/explain?track_code=${encodeURIComponent(trackCode)}&concept_tag=${encodeURIComponent(conceptTag)}`,
    ),
  ziaExplainByQuestion: (questionId: number) =>
    get<ZiaExplain>(`/api/zia/explain?question_id=${questionId}`),
  ziaCheckAnswer: (body: {
    concept_tag: string;
    track_code: string;
    question_id?: number;
    follow_up_question?: string;
    learner_answer: string;
  }) => post<ZiaCheckAnswer>("/api/zia/check-answer", body),
};

export { ApiError, API_URL };
