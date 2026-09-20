"use client";

import { use, useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { ScenarioIntro } from "@/components/ScenarioIntro";
import { ScenarioResult } from "@/components/ScenarioResult";
import { ScenarioStepRunner } from "@/components/ScenarioStepRunner";
import { useScenario } from "@/lib/scenarioStore";
import type { ScenarioListItem } from "@/lib/types";

export default function ScenarioRunnerPage({
  params,
}: {
  params: Promise<{ code: string; externalId: string }>;
}) {
  const { code, externalId } = use(params);
  const {
    scenarioExternalId,
    status,
    title,
    setupText,
    domainCode,
    result,
    error,
    start,
    resume,
    reset,
  } = useScenario();

  const [preview, setPreview] = useState<ScenarioListItem | null | "error">(null);

  // A stale attempt from a different scenario must not bleed into this page --
  // mirrors ExamPage's own reset-on-mismatch effect (app/tracks/[code]/exam/page.tsx).
  useEffect(() => {
    if (scenarioExternalId && scenarioExternalId !== externalId) reset();
  }, [externalId, scenarioExternalId, reset]);

  // Resume takes priority over showing the intro: if this browser already has an
  // in-progress attempt for this exact scenario (Section 15), continue it rather than
  // silently starting a second one.
  useEffect(() => {
    if (scenarioExternalId === externalId) return;
    void resume(externalId);
  }, [externalId, scenarioExternalId, resume]);

  // Title/domain for the pre-start screen, from the already-public discovery list --
  // never creates an attempt just by visiting the page.
  useEffect(() => {
    let cancelled = false;
    api
      .listScenarios(code)
      .then((list) => {
        if (cancelled) return;
        setPreview(list.find((s) => s.external_id === externalId) ?? "error");
      })
      .catch(() => {
        if (!cancelled) setPreview("error");
      });
    return () => {
      cancelled = true;
    };
  }, [code, externalId]);

  const backLink = (
    <Link
      href={`/tracks/${code}/scenarios`}
      className="text-xs text-[var(--color-muted)] hover:text-[var(--color-accent)]"
    >
      &larr; Back to Scenario Lab
    </Link>
  );

  if (status === "loading") {
    return (
      <main>
        {backLink}
        <p className="mt-6 text-sm text-[var(--color-muted)]">Loading&hellip;</p>
      </main>
    );
  }

  if (status === "error") {
    return (
      <main>
        {backLink}
        <div
          role="alert"
          className="mt-6 rounded-lg border border-[var(--color-warn)]/40 bg-[var(--color-warn)]/5 p-4 text-sm text-[var(--color-warn)]"
        >
          <p>{error ?? "This scenario is unavailable right now."}</p>
          <button
            type="button"
            onClick={() => void start(externalId)}
            className="mt-3 rounded-md border border-[var(--color-warn)]/50 px-3 py-1 text-xs"
          >
            Try again
          </button>
        </div>
      </main>
    );
  }

  if (status === "submitted" && result) {
    return (
      <main>
        <ScenarioResult
          title={title ?? ""}
          domainCode={domainCode ?? ""}
          scorePct={result.score_pct}
          masteryBand={result.mastery_band}
          trackCode={code}
        />
      </main>
    );
  }

  if (status === "situation" || status === "committing" || status === "consequence") {
    return (
      <main>
        <ScenarioStepRunner
          title={title ?? ""}
          domainCode={domainCode ?? ""}
          setupText={setupText ?? ""}
        />
      </main>
    );
  }

  // idle: nothing to resume -- the pre-start screen.
  if (preview === "error") {
    return (
      <main>
        {backLink}
        <p className="mt-6 rounded-lg border border-[var(--color-warn)]/40 bg-[var(--color-warn)]/5 p-4 text-sm text-[var(--color-warn)]">
          This scenario could not be found.
        </p>
      </main>
    );
  }

  return (
    <main>
      {backLink}
      <div className="mt-4">
        <ScenarioIntro
          title={preview?.title ?? "Scenario"}
          domainCode={preview?.domain_code ?? code}
          loading={(preview === null) as boolean}
          onBegin={() => void start(externalId)}
        />
      </div>
    </main>
  );
}
