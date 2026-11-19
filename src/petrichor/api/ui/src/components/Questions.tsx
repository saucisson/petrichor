import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import type { ApiClient } from "../api";
import type { Contract, Question } from "../types";

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

/* Step 2 - question cards with the three arbitration decisions. Formulas,
   families, triages, statuses, verdicts and camps are server data: rendered
   verbatim, never translated (spec §9 v1.3). */
export default function Questions({ hidden, client, sessionId, reloadToken }: QuestionsProps) {
  const { t } = useTranslation();
  const [contract, setContract] = useState<Contract | null>(null);

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
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
