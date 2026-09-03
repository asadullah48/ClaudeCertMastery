"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import type { ZiaExplain } from "@/lib/types";

/**
 * "Ask Zia" companion panel.
 *
 * A companion to the Claude-generated explanation, never a replacement. The panel is
 * driven entirely by whether the concept resolves to a real Agent Factory lesson: if
 * the backend reports `available: false` -- no mapping, tutor unavailable, or the
 * curriculum genuinely does not cover it -- the panel renders nothing at all rather
 * than showing an error. A tutor outage should be invisible to a candidate.
 */
export function AskZiaPanel({
  trackCode,
  conceptTag,
  questionId,
  label,
}: {
  trackCode: string;
  conceptTag?: string;
  questionId?: number;
  label?: string;
}) {
  const [state, setState] = useState<"idle" | "loading" | "ready" | "hidden">("idle");
  const [data, setData] = useState<ZiaExplain | null>(null);
  const [answer, setAnswer] = useState("");
  const [recorded, setRecorded] = useState<null | boolean>(null);

  async function open() {
    setState("loading");
    try {
      // Session first: the backend decides begin_session vs open_student_record, so
      // the learner is resumed rather than restarted on a second open.
      await api.ziaSession(`Cert Mastery ${trackCode}`);
      const result = questionId
        ? await api.ziaExplainByQuestion(questionId)
        : await api.ziaExplainByConcept(trackCode, conceptTag ?? "");

      if (!result.available) {
        setState("hidden");
        return;
      }
      setData(result);
      setState("ready");
    } catch {
      setState("hidden");
    }
  }

  async function submitAnswer() {
    if (!data?.concept_tag || !answer.trim()) return;
    try {
      const res = await api.ziaCheckAnswer({
        concept_tag: data.concept_tag,
        track_code: trackCode,
        question_id: questionId,
        follow_up_question: data.follow_up_question ?? "",
        learner_answer: answer,
      });
      setRecorded(res.recorded);
    } catch {
      setRecorded(false);
    }
  }

  if (state === "hidden") return null;

  if (state === "idle" || state === "loading") {
    return (
      <button
        onClick={open}
        disabled={state === "loading"}
        className="rounded-md border border-[var(--color-edge)] bg-[var(--color-surface)] px-3 py-2 text-sm transition hover:border-[var(--color-accent)] disabled:opacity-60"
      >
        {state === "loading" ? "Asking Zia..." : `Ask Zia${label ? `: ${label}` : ""}`}
      </button>
    );
  }

  return (
    <section className="rounded-lg border border-[var(--color-edge)] bg-[var(--color-surface)] p-5">
      <header className="mb-3 flex items-center justify-between gap-3">
        <h3 className="text-sm font-semibold">
          Zia on {data?.concept_label ?? "this concept"}
        </h3>
        <span className="text-xs text-[var(--color-muted)]">
          The AI Agent Factory
        </span>
      </header>

      <div className="whitespace-pre-wrap text-sm leading-relaxed text-[var(--color-muted)]">
        {data?.explanation}
      </div>

      {/* Every answer is shown with where it came from. An explanation with no
          traceable source is the exact failure mode this platform teaches
          candidates to distrust. */}
      {data?.citations?.length ? (
        <div className="mt-4 border-t border-[var(--color-edge)] pt-3">
          <div className="mb-2 text-xs uppercase tracking-widest text-[var(--color-muted)]">
            Source
          </div>
          <ul className="space-y-1">
            {data.citations.map((c, i) => (
              <li key={`${c.slug}-${i}`} className="text-xs">
                <a
                  href={c.url ?? "#"}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="font-mono text-[var(--color-accent)] hover:underline"
                >
                  {c.slug}
                </a>
                {c.heading_path && (
                  <span className="ml-2 text-[var(--color-muted)]">
                    {c.heading_path}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {data?.follow_up_question && (
        <div className="mt-4 border-t border-[var(--color-edge)] pt-4">
          <label className="block text-sm">{data.follow_up_question}</label>
          <textarea
            value={answer}
            onChange={(e) => setAnswer(e.target.value)}
            rows={3}
            className="mt-2 w-full rounded border border-[var(--color-edge)] bg-[var(--color-ink)] p-2 text-sm outline-none focus:border-[var(--color-accent)]"
            placeholder="Your answer..."
          />
          <div className="mt-2 flex items-center gap-3">
            <button
              onClick={submitAnswer}
              disabled={!answer.trim() || recorded === true}
              className="rounded-md border border-[var(--color-edge)] px-3 py-1.5 text-sm transition hover:border-[var(--color-accent)] disabled:opacity-50"
            >
              {recorded === true ? "Recorded" : "Send to Zia"}
            </button>
            {recorded === false && (
              <span className="text-xs text-[var(--color-warn)]">
                Could not record just now.
              </span>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
