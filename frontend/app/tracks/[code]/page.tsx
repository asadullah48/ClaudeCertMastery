import Link from "next/link";
import { notFound } from "next/navigation";
import { api } from "@/lib/api";
import { AskZiaPanel } from "@/components/AskZiaPanel";
import type { Blueprint, Track, ZiaConcepts } from "@/lib/types";

export const dynamic = "force-dynamic";


export default async function TrackDetail({
  params,
}: {
  params: Promise<{ code: string }>;
}) {
  const { code } = await params;

  let track: Track;
  let blueprint: Blueprint;
  // Availability is decided by the mapping table, not by a hardcoded track list, so
  // widening Ask Zia to a new track is a data change with no frontend edit.
  let zia: ZiaConcepts | null = null;
  try {
    [track, blueprint] = await Promise.all([
      api.getTrack(code),
      api.getBlueprint(code),
    ]);
  } catch {
    notFound();
  }

  try {
    zia = await api.ziaConcepts(code);
  } catch {
    zia = null; // tutor unreachable: panel simply does not render
  }

  return (
    <main>
      <Link
        href="/"
        className="text-xs text-[var(--color-muted)] hover:text-[var(--color-accent)]"
      >
        &larr; All tracks
      </Link>

      <header className="mt-4 mb-8">
        <span className="rounded bg-[var(--color-edge)] px-2 py-0.5 font-mono text-xs">
          {track.code}
        </span>
        <h1 className="mt-3 text-2xl font-semibold tracking-tight">{track.name}</h1>
        <p className="mt-2 max-w-2xl text-sm leading-relaxed text-[var(--color-muted)]">
          {track.description}
        </p>
      </header>

      <section className="mb-8 grid grid-cols-2 gap-3 sm:grid-cols-4">
        {[
          ["Items", String(track.item_count)],
          ["Duration", `${track.duration_minutes} min`],
          ["Pass score", `${track.pass_scaled_score}/1000`],
          ["Valid for", `${track.validity_months} months`],
        ].map(([label, value]) => (
          <div
            key={label}
            className="rounded-lg border border-[var(--color-edge)] bg-[var(--color-surface)] p-4"
          >
            <div className="text-xs text-[var(--color-muted)]">{label}</div>
            <div className="mt-1 text-lg font-semibold">{value}</div>
          </div>
        ))}
      </section>

      <section>
        <h2 className="mb-3 text-xs font-medium uppercase tracking-widest text-[var(--color-muted)]">
          Exam blueprint
        </h2>
        <div className="overflow-hidden rounded-lg border border-[var(--color-edge)]">
          <table className="w-full text-sm">
            <thead className="bg-[var(--color-surface)] text-xs text-[var(--color-muted)]">
              <tr>
                <th className="px-4 py-3 text-left font-medium">Domain</th>
                <th className="px-4 py-3 text-right font-medium">Weight</th>
                <th className="px-4 py-3 text-right font-medium">Items</th>
                <th className="px-4 py-3 text-right font-medium">Bank</th>
              </tr>
            </thead>
            <tbody>
              {blueprint.domains.map((d) => (
                <tr key={d.code} className="border-t border-[var(--color-edge)]">
                  <td className="px-4 py-3">
                    <span className="font-mono text-xs text-[var(--color-muted)]">
                      {d.code}
                    </span>
                    <span className="ml-2">{d.name}</span>
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums">
                    {d.weight_pct.toFixed(0)}%
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums font-medium">
                    {d.items_at_full_length}
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums text-[var(--color-muted)]">
                    {d.questions_available}
                  </td>
                </tr>
              ))}
            </tbody>
            <tfoot className="border-t border-[var(--color-edge)] bg-[var(--color-surface)]">
              <tr>
                <td className="px-4 py-3 text-xs text-[var(--color-muted)]">Total</td>
                <td className="px-4 py-3 text-right tabular-nums">
                  {(blueprint.total_weight_bps / 100).toFixed(0)}%
                </td>
                <td className="px-4 py-3 text-right tabular-nums font-semibold">
                  {blueprint.domains.reduce(
                    (n, d) => n + d.items_at_full_length,
                    0,
                  )}
                </td>
                <td className="px-4 py-3 text-right tabular-nums text-[var(--color-muted)]">
                  {blueprint.domains.reduce(
                    (n, d) => n + d.questions_available,
                    0,
                  )}
                </td>
              </tr>
            </tfoot>
          </table>
        </div>
      </section>

      {zia && zia.concepts.length > 0 && (
        <section className="mt-8">
          <h2 className="mb-3 text-xs font-medium uppercase tracking-widest text-[var(--color-muted)]">
            Ask Zia &mdash; companion tutor
          </h2>
          <p className="mb-4 text-sm text-[var(--color-muted)]">
            Optional. Claude-generated explanations remain the default everywhere; Zia
            teaches the same concepts from The AI Agent Factory curriculum, with a
            source link on every answer.
          </p>
          <div className="flex flex-wrap gap-2">
            {zia.concepts.map((c) => (
              <AskZiaPanel
                key={c.concept_tag}
                trackCode={code}
                conceptTag={c.concept_tag}
                label={c.label}
              />
            ))}
          </div>

          {zia.unmapped.length > 0 && (
            <p className="mt-4 text-xs text-[var(--color-muted)]">
              No curriculum lesson yet for: {zia.unmapped.join(", ")}. The panel stays
              hidden for those rather than pointing somewhere that would not answer the
              question.
            </p>
          )}
        </section>
      )}

      <section className="mt-8 rounded-lg border border-[var(--color-edge)] bg-[var(--color-surface)] p-5">
        {track.is_seeded ? (
          <>
            <h2 className="text-sm font-medium">Ready to sit this exam?</h2>
            <p className="mt-1 max-w-xl text-sm leading-relaxed text-[var(--color-muted)]">
              {track.question_count} questions are authored for {track.code}. Choose a
              full sitting or a shorter drill &mdash; the domain weighting above holds at
              every length.
            </p>
            <Link
              href={`/tracks/${code}/exam`}
              className="mt-4 inline-block rounded-md bg-[var(--color-accent)] px-4 py-2 text-sm font-medium text-[var(--color-ink)]"
            >
              Start a practice exam
            </Link>
          </>
        ) : (
          <p className="text-sm text-[var(--color-muted)]">
            The blueprint for {track.code} is published, but its question bank is not
            authored yet. The generator, scoring engine and runner are ready for it
            &mdash; only the items are outstanding.
          </p>
        )}
      </section>
    </main>
  );
}
