import Link from "next/link";
import { notFound } from "next/navigation";
import { ReadinessSummary } from "@/components/ReadinessSummary";
import { api } from "@/lib/api";
import { serverToken } from "@/lib/serverToken";
import type { Track, TrackReadiness } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function ReadinessPage({
  params,
}: {
  params: Promise<{ code: string }>;
}) {
  const { code } = await params;

  let track: Track;
  try {
    track = await api.getTrack(code);
  } catch {
    notFound();
  }

  let readiness: TrackReadiness | null = null;
  try {
    readiness = await api.getReadiness(code, await serverToken());
  } catch {
    readiness = null; // shown as an unavailable state, never a fabricated one
  }

  return (
    <main>
      <Link
        href={`/tracks/${code}`}
        className="text-xs text-[var(--color-muted)] hover:text-[var(--color-accent)]"
      >
        &larr; Back to {code}
      </Link>

      <header className="mt-4 mb-8">
        <span className="rounded bg-[var(--color-edge)] px-2 py-0.5 font-mono text-xs">
          {track.code}
        </span>
        <h1 className="mt-3 text-2xl font-semibold tracking-tight">Your readiness</h1>
        <p className="mt-2 max-w-2xl text-sm leading-relaxed text-[var(--color-muted)]">
          Built only from your own evidence: completed practice exams and your first
          attempt at each scenario. Retakes are practice and do not change this view.
        </p>
      </header>

      {readiness ? (
        <ReadinessSummary readiness={readiness} />
      ) : (
        <p className="rounded-lg border border-[var(--color-edge)] bg-[var(--color-surface)] p-5 text-sm text-[var(--color-muted)]">
          Readiness is not available right now.
        </p>
      )}
    </main>
  );
}
