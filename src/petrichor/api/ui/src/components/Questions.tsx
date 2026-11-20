import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import type { ApiClient } from "../api";
import type { Contract, FormulaEditResponse, FormulaReading, Question } from "../types";

const CONTRACT_ACCEPT = "application/contract+json; version=1.0";
const DECISIONS: { id: string; labelKey: "refuse" | "accept" | "verifyFirst" }[] = [
  { id: "refuse", labelKey: "refuse" },
  { id: "accept", labelKey: "accept" },
  { id: "verify-first", labelKey: "verifyFirst" },
];

interface QuestionsProps {
  hidden: boolean;
  client: ApiClient;
  sessionId: string;
  reloadToken: number;
}

function CrossStatus({ question }: { question: Question }) {
  const { t } = useTranslation();
  if (question.cross_status === "verified_by_n") {
    return (
      <p className="cross-status">{t("questions.cross.verifiedByN", { count: question.verified_by })}</p>
    );
  }
  if (question.cross_status === "controversial") {
    return (
      <p className="cross-status">
        {t("questions.cross.controversial")}
        <ul className="controversy-camps">
          {question.proofs
            .filter((proof) => proof.status === "done" && proof.verdict)
            .map((proof) => (
              <li key={proof.tool_version}>
                {t("questions.cross.proofLine", {
                  tool: proof.tool_version,
                  verdict: proof.verdict,
                })}
              </li>
            ))}
        </ul>
      </p>
    );
  }
  return <p className="cross-status">{t("questions.cross.notVerified")}</p>;
}

/* Formula editor (P37, issue #37): edits go through POST .../formula, the
   inverse reading comes back labeled provenance: llm, the edit is journaled
   append-only server-side and the contract is reloaded - the re-verification
   runs through the standard check chain, never bypassed, and no verdict is
   computed client-side (spec §9, §19.4). Reading texts, notes and API error
   details are server data: rendered verbatim, never translated. */
function FormulaEditor({
  client,
  sessionId,
  question,
  onEdited,
}: {
  client: ApiClient;
  sessionId: string;
  question: Question;
  onEdited: () => void;
}) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState(question.formula);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reading, setReading] = useState<FormulaReading | null>(null);
  const [done, setDone] = useState(false);

  const save = async () => {
    setBusy(true);
    setError(null);
    setDone(false);
    try {
      const response = await client.request(
        "/v1/sessions/" + sessionId + "/questions/" + question.question_id + "/formula",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ formula: draft }),
        },
      );
      if (response.status !== 201) {
        // The problem detail is server-produced text: displayed verbatim.
        let detail = "";
        try {
          const problem = (await response.json()) as { detail?: string };
          detail = problem.detail ?? "";
        } catch {
          detail = await response.text();
        }
        setError(t("questions.editError", { status: response.status, detail }));
        return;
      }
      const payload = (await response.json()) as FormulaEditResponse;
      setReading(payload.reading);
      setDone(true);
      onEdited();
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : String(saveError));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="formula-editor">
      <label htmlFor={"formula-" + question.question_id}>{t("questions.editLabel")}</label>
      <input
        id={"formula-" + question.question_id}
        type="text"
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
      />
      <button type="button" disabled={busy} onClick={() => void save()}>
        {t("questions.editSave")}
      </button>
      {error !== null ? <p className="edit-error">{error}</p> : null}
      {done ? <p className="edit-done">{t("questions.editDone")}</p> : null}
      {reading !== null ? (
        <div className="formula-reading">
          <h4>{t("questions.readingTitle")}</h4>
          {reading.degraded === true ? (
            <p>{reading.note ?? ""}</p>
          ) : reading.reading !== undefined ? (
            <>
              <p>
                {reading.reading}{" "}
                <span className="narration-provenance">
                  (provenance : {reading.provenance ?? "llm"})
                </span>
              </p>
              {reading.not_says !== undefined && reading.not_says !== "" ? (
                <p>{t("questions.readingNotSays", { notSays: reading.not_says })}</p>
              ) : null}
            </>
          ) : (
            <p>{t("questions.readingNone")}</p>
          )}
        </div>
      ) : null}
    </div>
  );
}

/* Step 2 - question cards with the three arbitration decisions. Formulas,
   families, triages, statuses, verdicts and camps are server data: rendered
   verbatim, never translated (spec §9 v1.3). */
export default function Questions({ hidden, client, sessionId, reloadToken }: QuestionsProps) {
  const { t } = useTranslation();
  const [contract, setContract] = useState<Contract | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);

  const loadQuestions = useCallback(async () => {
    if (!sessionId) return;
    const response = await client.request("/v1/sessions/" + sessionId + "/contract", {
      headers: { Accept: CONTRACT_ACCEPT },
    });
    if (response.status !== 200) return;
    setContract((await response.json()) as Contract);
  }, [client, sessionId]);

  useEffect(() => {
    if (!hidden) {
      void loadQuestions();
    }
  }, [hidden, loadQuestions, reloadToken]);

  const decide = async (questionId: string, decision: string) => {
    await client.postJson("/v1/sessions/" + sessionId + "/arbitration-requests", {
      question_id: questionId,
      decision,
    });
    await loadQuestions();
  };

  return (
    <section id="step-questions" hidden={hidden} aria-labelledby="h-questions">
      <h2 id="h-questions">{t("questions.title")}</h2>
      <p>{t("questions.intro")}</p>
      <ul id="question-cards">
        {contract?.questions.map((question) => (
          <li className="question-card" key={question.question_id}>
            <p>
              {t("questions.meta", {
                formula: question.formula,
                family: question.family,
                triage: question.triage,
              })}
            </p>
            <p className="status">{t("questions.status", { status: question.status })}</p>
            <CrossStatus question={question} />
            <div className="decisions">
              {DECISIONS.map((decision) => (
                <button
                  key={decision.id}
                  type="button"
                  onClick={() => void decide(question.question_id, decision.id)}
                >
                  {t("questions.decisions." + decision.labelKey)}
                </button>
              ))}
              <button
                type="button"
                onClick={() =>
                  setEditingId(editingId === question.question_id ? null : question.question_id)
                }
              >
                {t("questions.edit")}
              </button>
            </div>
            {editingId === question.question_id ? (
              <FormulaEditor
                client={client}
                sessionId={sessionId}
                question={question}
                onEdited={() => void loadQuestions()}
              />
            ) : null}
          </li>
        ))}
      </ul>
    </section>
  );
}
