import { api, API_URL } from "@/lib/api";
import { TrackCard } from "@/components/TrackCard";
import type { Track } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function Home() {
  let tracks: Track[] = [];
  let error: string | null = null;

  try {
    tracks = await api.listTracks();
  } catch {
    error = `Could not reach the API at ${API_URL}.`;
  }

  return (
    <main>
      <header className="mb-10">
        <h1 className="text-2xl font-semibold tracking-tight">
          Claude Cert Mastery
        </h1>
        <p className="mt-2 max-w-2xl text-sm leading-relaxed text-[var(--color-muted)]">
          Blueprint-weighted practice exams for the Claude certification tracks.
          Every exam mirrors the published domain weighting, and results are reported
          on the real 100&ndash;1000 scale with a 720 pass line.
        </p>
      </header>

      {error ? (
        <div className="rounded-lg border border-[var(--color-warn)]/40 bg-[var(--color-warn)]/5 p-5">
          <p className="text-sm font-medium text-[var(--color-warn)]">{error}</p>
          <p className="mt-2 text-sm text-[var(--color-muted)]">
            Start the backend, then reload:
          </p>
          <pre className="mt-3 overflow-x-auto rounded bg-[var(--color-ink)] p-3 font-mono text-xs">
            cd backend{"\n"}uvicorn app.main:app --reload
          </pre>
        </div>
      ) : (
        <>
          <h2 className="mb-4 text-xs font-medium uppercase tracking-widest text-[var(--color-muted)]">
            Certification tracks
          </h2>
          <div className="grid gap-4 sm:grid-cols-2">
            {tracks.map((track) => (
              <TrackCard key={track.code} track={track} />
            ))}
          </div>
        </>
      )}

      <footer className="mt-12 border-t border-[var(--color-edge)] pt-6 text-xs text-[var(--color-muted)]">
        Session 1 (Foundation). The exam runner arrives in Session 2 &mdash; track
        selection and blueprint inspection are live now.
      </footer>
    </main>
  );
}
