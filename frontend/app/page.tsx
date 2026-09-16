import { api } from "@/lib/api";
import { TrackCard } from "@/components/TrackCard";
import type { Track } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function Home() {
  let tracks: Track[] = [];
  let error: string | null = null;

  try {
    tracks = await api.listTracks();
  } catch {
    // Never surface the internal API URL (e.g. a localhost default) to a real
    // visitor -- it is meaningless to them and looks like a broken deployment
    // rather than a temporary outage.
    error = "Could not reach the exam platform right now. Please try again in a few minutes.";
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
          as a Practice score on a 100&ndash;1000 scale &mdash; ClaudeCertMastery&apos;s
          own estimated-readiness model, not an official Anthropic score.
        </p>
      </header>

      {error ? (
        <div className="rounded-lg border border-[var(--color-warn)]/40 bg-[var(--color-warn)]/5 p-5">
          <p className="text-sm font-medium text-[var(--color-warn)]">{error}</p>
          {process.env.NODE_ENV === "development" && (
            <>
              <p className="mt-2 text-sm text-[var(--color-muted)]">
                Start the backend, then reload:
              </p>
              <pre className="mt-3 overflow-x-auto rounded bg-[var(--color-ink)] p-3 font-mono text-xs">
                cd backend{"\n"}uvicorn app.main:app --reload
              </pre>
            </>
          )}
          <p className="mt-3 text-xs text-[var(--color-muted)]">
            The exam runner, scaled scoring and AI remediation are not reachable while
            this message is showing.
          </p>
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
          <footer className="mt-12 border-t border-[var(--color-edge)] pt-6 text-xs text-[var(--color-muted)]">
            The exam runner, scaled scoring and AI remediation are live. CCAO-F is the
            seeded track; the other three publish their blueprint while their question
            banks are authored.
          </footer>
        </>
      )}
    </main>
  );
}
