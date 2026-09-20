import Link from "next/link";
import { notFound } from "next/navigation";
import { api } from "@/lib/api";
import type { ScenarioListItem, Track } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function ScenarioListPage({
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

  let scenarios: ScenarioListItem[] = [];
  try {
    scenarios = await api.listScenarios(code);
  } catch {
    scenarios = []; // discovery degrades to an empty list, never an error page
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
        <h1 className="mt-3 text-2xl font-semibold tracking-tight">Scenario Lab</h1>
        <p className="mt-2 max-w-2xl text-sm leading-relaxed text-[var(--color-muted)]">
          Short, decision-driven exercises grounded in the same blueprint domains as
          the exam. Each one asks you to reason through a realistic situation, not
          recall a definition.
        </p>
      </header>

      {scenarios.length === 0 ? (
        <p className="rounded-lg border border-[var(--color-edge)] bg-[var(--color-surface)] p-5 text-sm text-[var(--color-muted)]">
          No scenarios are published for {track.code} yet.
        </p>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2">
          {scenarios.map((s) => (
            <Link
              key={s.external_id}
              href={`/tracks/${code}/scenarios/${s.external_id}`}
              className="rounded-lg border border-[var(--color-edge)] bg-[var(--color-surface)] p-5 transition-colors hover:border-[var(--color-accent)]"
            >
              <span className="rounded bg-[var(--color-edge)] px-2 py-0.5 font-mono text-xs">
                {s.domain_code}
              </span>
              <div className="mt-2 text-sm font-medium">{s.title}</div>
            </Link>
          ))}
        </div>
      )}
    </main>
  );
}
