import type { Blueprint, Health, Track } from "./types";

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

export const api = {
  health: () => get<Health>("/health"),
  listTracks: () => get<Track[]>("/tracks"),
  getTrack: (code: string) => get<Track>(`/tracks/${code}`),
  getBlueprint: (code: string) => get<Blueprint>(`/tracks/${code}/blueprint`),
};

export { ApiError, API_URL };
