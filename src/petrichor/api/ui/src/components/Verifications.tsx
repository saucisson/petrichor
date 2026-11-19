import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import type { ApiClient } from "../api";
import type { Budget, Run, Session, Witness } from "../types";

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
function WitnessReader({ client, runId }: { client: ApiClient; runId: string }) {
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
  if (witness === null) return null;
  const narrations = new Map((witness.narrations ?? []).map((n) => [n.step_index, n]));
  return (
    <div className="witness-reader">
      <p className="witness-title">
        {t("verifications.witnessTitle", { id: witness.witness_id, kind: witness.kind })}
        {witness.cyclic
          ? t("verifications.witnessCyclic", { edge: witness.back_edge ?? "" })
          : t("verifications.witnessAcyclic")}
      </p>
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

  const loadRuns = useCallback(async () => {
    if (!sessionId) return;
    const sessionData = await client.getJson<Session>("/v1/sessions/" + sessionId);
    setSession(sessionData);
    const budgetResponse = await client.request("/v1/sessions/" + sessionId + "/budget");
    if (budgetResponse.status === 200) {
      setBudget((await budgetResponse.json()) as Budget);
    }
    const loadedRuns: Run[] = [];
    for (const runId of sessionData.run_ids) {
      loadedRuns.push(await client.getJson<Run>("/v1/runs/" + runId));
    }
    setRuns(loadedRuns);
  }, [client, sessionId]);

  useEffect(() => {
    if (!hidden) {
      void loadRuns();
    }
  }, [hidden, loadRuns, reloadToken]);

  return (
    <section id="step-verifications" hidden={hidden} aria-labelledby="h-verifications">
      <h2 id="h-verifications">{t("verifications.title")}</h2>
      <p>{t("verifications.intro")}</p>
      <ul id="run-list">
        {budget !== null ? (
          <li className="budget-line">
            {t("verifications.budget", {
              consumed: budget.consumed_seconds.toFixed(3),
              cap: budget.budget_seconds.toFixed(3),
              remaining: budget.remaining_seconds.toFixed(3),
            })}
          </li>
        ) : null}
        {runs.map((run) => (
          <li key={run.run_id}>
            <span>
              {t("verifications.runLine", {
                runId: run.run_id,
                family: run.family,
                kind: run.kind,
                status: run.status,
                triage: run.triage.kind,
              })}{" "}
            </span>
            <span className="run-verdict">
              {run.output?.verdict !== undefined && run.output?.verdict !== null
                ? String(run.output.verdict)
                : "—"}
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
