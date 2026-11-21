import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { ApiClient } from "../api";
import VerdictBadge from "./VerdictBadge";
import type { Budget, Run, Session, Witness } from "../types";

/* P78: runs in these statuses are polled every 3 s (see below). */
const ACTIVE_RUN_STATUSES = new Set(["running", "pending", "queued"]);
const POLL_INTERVAL_MS = 3000;

interface VerificationsProps {
  hidden: boolean;
  client: ApiClient;
  sessionId: string;
  reloadToken: number;
}

/* Witness step reader (spec §16): the structure comes from the tool, the
   narration is an LLM proposition labeled provenance: llm - never a verdict.
   The fired transition of each step is highlighted. Witness ids, kinds,
   markings, transitions, narrations and provenance labels are server data:
   rendered verbatim, never translated (spec §9 v1.3). */
export function WitnessReader({ client, runId }: { client: ApiClient; runId: string }) {
  const { t } = useTranslation();
  const [witness, setWitness] = useState<Witness | null>(null);
  const [unavailable, setUnavailable] = useState<number | null>(null);

  useEffect(() => {
    void (async () => {
      const response = await client.request("/v1/runs/" + runId + "/witness");
      if (response.status !== 200) {
        setUnavailable(response.status);
        return;
      }
      setWitness((await response.json()) as Witness);
    })();
  }, [client, runId]);

  if (unavailable !== null) {
    return (
      <div className="witness-reader">{t("verifications.witnessUnavailable", { status: unavailable })}</div>
    );
  }
  const narrations = new Map((witness?.narrations ?? []).map((n) => [n.step_index, n]));
  // P78: the witness is folded in a <details>; the summary carries the
  // witness title (id, kind, cyclicity) once loaded.
  return (
    <details className="witness-details">
      <summary className="witness-summary">
        {witness === null
          ? t("verifications.loading")
          : t("verifications.witnessTitle", { id: witness.witness_id, kind: witness.kind }) +
            (witness.cyclic
              ? t("verifications.witnessCyclic", { edge: witness.back_edge ?? "" })
              : t("verifications.witnessAcyclic"))}
      </summary>
      {witness === null ? null : (
        <div className="witness-reader">
          <ol className="witness-steps">
        {witness.steps.map((step) => {
          const narration = narrations.get(step.index);
          return (
            <li key={step.index}>
              <span className="witness-marking">
                {t("verifications.marking", { marking: step.marking.join(", ") })}
              </span>
              {step.fired !== null ? (
                <>
                  {" "}
                  <mark className="fired-transition">
                    {t("verifications.fired", { transition: step.fired })}
                  </mark>
                </>
              ) : null}
              {narration !== undefined ? (
                <p className="witness-narration">
                  {narration.text}{" "}
                  <span className="narration-provenance">
                    {t("verifications.narrationProvenance", { provenance: narration.provenance })}
                  </span>
                </p>
              ) : null}
            </li>
          );
        })}
          </ol>
        </div>
      )}
    </details>
  );
}

/* Step 3 - the session's runs with the live per-session MC budget (spec §12.4:
   consumed / cap, no carry-over) and the witness reader. Run ids, families,
   kinds, statuses, triages and verdicts are server data: verbatim, never
   translated. */
export default function Verifications({ hidden, client, sessionId, reloadToken }: VerificationsProps) {
  const { t } = useTranslation();
  const [session, setSession] = useState<Session | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [budget, setBudget] = useState<Budget | null>(null);
  // P76: loading / error / empty are explicit states - never a silent return.
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  /* `silent` is used by the 3 s polling of active runs: no loading flash,
     and a poll failure keeps the last known data instead of blanking the
     step. */
  const loadRuns = useCallback(
    async (silent: boolean) => {
    if (!sessionId) return;
    if (!silent) setLoading(true);
    if (!silent) setLoadError(null);
    try {
      const sessionData = await client.getJson<Session>("/v1/sessions/" + sessionId);
      setSession(sessionData);
      const budgetResponse = await client.request("/v1/sessions/" + sessionId + "/budget");
      if (budgetResponse.status === 200) {
        setBudget((await budgetResponse.json()) as Budget);
      } else {
        setBudget(null);
      }
      const loadedRuns: Run[] = [];
      for (const runId of sessionData.run_ids) {
        loadedRuns.push(await client.getJson<Run>("/v1/runs/" + runId));
      }
      setRuns(loadedRuns);
    } catch (loadFailure) {
      // The problem detail is server-produced text: displayed verbatim.
      if (!silent) {
        setLoadError(loadFailure instanceof Error ? loadFailure.message : String(loadFailure));
        setRuns([]);
      }
    } finally {
      if (!silent) setLoading(false);
    }
  },
    [client, sessionId],
  );

  useEffect(() => {
    if (!hidden) {
      void loadRuns(false);
    }
  }, [hidden, loadRuns, reloadToken]);

  /* P78: poll the active runs (running/pending/queued) every 3 s. App keeps
     every step mounted (hidden prop), so the polling continues while the
     user navigates to another step. */
  const activeRef = useRef(false);
  useEffect(() => {
    activeRef.current = runs.some((run) => ACTIVE_RUN_STATUSES.has(run.status));
  }, [runs]);
  useEffect(() => {
    const interval = setInterval(() => {
      if (activeRef.current) {
        void loadRuns(true);
      }
    }, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [loadRuns]);

  return (
    <section id="step-verifications" hidden={hidden} aria-labelledby="h-verifications">
      <h2 id="h-verifications">{t("verifications.title")}</h2>
      <p>{t("verifications.intro")}</p>
      {loading ? (
        <p className="message message-info" role="status">
          {t("verifications.loading")}
        </p>
      ) : null}
      {loadError !== null ? (
        <p className="message message-error" role="alert">
          {loadError}
        </p>
      ) : null}
      {!loading && loadError === null && runs.length === 0 ? (
        <p className="message message-info">{t("verifications.empty")}</p>
      ) : null}
      <ul id="run-list">
        {budget !== null ? (
          <li className="budget-line budget-card">
            {t("verifications.budget", {
              consumed: budget.consumed_seconds.toFixed(3),
              cap: budget.budget_seconds.toFixed(3),
              remaining: budget.remaining_seconds.toFixed(3),
            })}
          </li>
        ) : null}
        {runs.map((run) => (
          <li key={run.run_id} className="run-row">
            <span className="run-id">{run.run_id}</span>
            <span className="badge badge-family">{run.family}</span>
            <span className="badge badge-kind">{run.kind}</span>
            <span className="badge badge-triage">{run.triage.kind}</span>
            <span className={"badge run-status st-" + run.status}>{run.status}</span>
            <span className="run-verdict">
              <VerdictBadge verdict={run.output?.verdict ?? null} />
            </span>
            {run.output?.witness !== undefined && run.output?.witness !== null ? (
              <WitnessReader client={client} runId={run.run_id} />
            ) : null}
          </li>
        ))}
      </ul>
      {session === null ? null : null}
    </section>
  );
}
