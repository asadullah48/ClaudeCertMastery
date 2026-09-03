import Link from "next/link";
import { notFound } from "next/navigation";
import { api } from "@/lib/api";
import type { Blueprint, Track } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function TrackDetail({
  params,
}: {
  params: Promise<{ code: string }>;
}) {
  const { code } = await params;

  let track: Track;
  let blueprint: Blueprint;
  try {
    [track, blueprint] = await Promise.all([
      api.getTrack(code),
      api.getBlueprint(code),
    ]);
  } catch {
    notFound();
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

      <p className="mt-6 rounded-lg border border-[var(--color-edge)] bg-[var(--color-surface)] p-4 text-sm text-[var(--color-muted)]">
        The exam runner ships in Session 2. The generator, scoring engine and question
        bank behind this blueprint are complete and tested.
      </p>
    </main>
  );
}
