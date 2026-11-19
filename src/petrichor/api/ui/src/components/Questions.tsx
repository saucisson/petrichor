import { useCallback, useEffect, useState } from "react";
import type { ApiClient } from "../api";
import type { Contract, Question } from "../types";

const CONTRACT_ACCEPT = "application/contract+json; version=1.0";
const DECISIONS: { id: string; label: string }[] = [
  { id: "refuse", label: "Impossible" },
  { id: "accept", label: "C'est normal" },
  { id: "verify-first", label: "Vérifier d'abord" },
];

interface QuestionsProps {
  hidden: boolean;
  client: ApiClient;
  sessionId: string;
  reloadToken: number;
}

function CrossStatus({ question }: { question: Question }) {
  if (question.cross_status === "verified_by_n") {
    return <p className="cross-status">Vérifiée par {question.verified_by} outils.</p>;
  }
  if (question.cross_status === "controversial") {
    return (
      <p className="cross-status">
        Clause controversée — remontée à l'ingénieur, jamais tranchée ici.
        <ul className="controversy-camps">
          {question.proofs
            .filter((proof) => proof.status === "done" && proof.verdict)
            .map((proof) => (
              <li key={proof.tool_version}>
                {proof.tool_version} : verdict {proof.verdict}
              </li>
            ))}
        </ul>
      </p>
    );
  }
  return <p className="cross-status">Non vérifiée.</p>;
}

/* Step 2 - question cards with the three arbitration decisions. */
export default function Questions({ hidden, client, sessionId, reloadToken }: QuestionsProps) {
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
      <h2 id="h-questions">Questions statiques</h2>
      <p>Cartes-question : arbitrez chaque question (Impossible / C'est normal / Vérifier d'abord).</p>
      <ul id="question-cards">
        {contract?.questions.map((question) => (
          <li className="question-card" key={question.question_id}>
            <p>
              {question.formula} ({question.family}, triage {question.triage})
            </p>
            <p className="status">Statut : {question.status}</p>
            <CrossStatus question={question} />
            <div className="decisions">
              {DECISIONS.map((decision) => (
                <button
                  key={decision.id}
                  type="button"
                  onClick={() => void decide(question.question_id, decision.id)}
                >
                  {decision.label}
                </button>
              ))}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
