import Link from "next/link";
import type { Track } from "@/lib/types";

/**
 * One certification track.
 *
 * Unseeded tracks render in a visibly inactive state rather than being hidden (D-9),
 * so the catalog reflects the real certification landscape and is honest about which
 * banks are authored.
 */
export function TrackCard({ track }: { track: Track }) {
  const available = track.is_seeded && track.question_count > 0;

  const body = (
    <div
      className={`h-full rounded-lg border p-5 transition ${
        available
          ? "border-[var(--color-edge)] bg-[var(--color-surface)] hover:border-[var(--color-accent)]"
          : "border-[var(--color-edge)]/60 bg-[var(--color-surface)]/40"
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        <span className="rounded bg-[var(--color-edge)] px-2 py-0.5 font-mono text-xs tracking-wide">
          {track.code}
        </span>
        {available ? (
          <span className="text-xs text-[var(--color-pass)]">
            {track.question_count} questions
          </span>
        ) : (
          <span className="text-xs text-[var(--color-warn)]">Content coming</span>
        )}
      </div>

      <h2
        className={`mt-3 text-base font-semibold ${
          available ? "" : "text-[var(--color-muted)]"
        }`}
      >
        {track.name}
      </h2>

      <p className="mt-2 line-clamp-3 text-sm leading-relaxed text-[var(--color-muted)]">
        {track.description}
      </p>

      {available && (
        <dl className="mt-4 grid grid-cols-3 gap-2 border-t border-[var(--color-edge)] pt-4 text-xs">
          <div>
            <dt className="text-[var(--color-muted)]">Items</dt>
            <dd className="mt-0.5 font-medium">{track.item_count}</dd>
          </div>
          <div>
            <dt className="text-[var(--color-muted)]">Time</dt>
            <dd className="mt-0.5 font-medium">{track.duration_minutes} min</dd>
          </div>
          <div>
            <dt className="text-[var(--color-muted)]">Pass</dt>
            <dd className="mt-0.5 font-medium">{track.pass_scaled_score}/1000</dd>
          </div>
        </dl>
      )}
    </div>
  );

  return available ? (
    <Link href={`/tracks/${track.code}`} className="block">
      {body}
    </Link>
  ) : (
    <div className="cursor-not-allowed">{body}</div>
  );
}
