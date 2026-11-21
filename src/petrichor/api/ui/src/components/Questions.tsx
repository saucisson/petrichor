import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { readProblemDetail, type ApiClient } from "../api";
import LlmBusy from "./LlmBusy";
import VerdictBadge from "./VerdictBadge";
import type {
  Contract,
  ConvergenceList,
  FormulaEditResponse,
  FormulaReading,
  Question,
} from "../types";

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
                {proof.tool_version} <VerdictBadge verdict={proof.verdict} />
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
      <button type="button" className="btn btn-primary" disabled={busy} onClick={() => void save()}>
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
  const [convergences, setConvergences] = useState<ConvergenceList | null>(null);
  const [fusionMessage, setFusionMessage] = useState<string | null>(null);
  const [refusedIds, setRefusedIds] = useState<Set<string>>(new Set());
  // P76: loading / error / empty are explicit states - never a silent return.
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  // P78: presentation-only filter bar (search by formula, family, triage,
  // status, sort by id/family/triage). The filtering never hides data from
  // the server - it only narrows the view of the already-loaded contract.
  const [query, setQuery] = useState("");
  const [familyFilter, setFamilyFilter] = useState("");
  const [triageFilter, setTriageFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [sortKey, setSortKey] = useState<"id" | "family" | "triage">("id");

  const loadQuestions = useCallback(async () => {
    if (!sessionId) return;
    setLoading(true);
    setLoadError(null);
    try {
      const response = await client.request("/v1/sessions/" + sessionId + "/contract", {
        headers: { Accept: CONTRACT_ACCEPT },
      });
      if (response.status !== 200) {
        // The problem detail is server-produced text: displayed verbatim.
        setLoadError(await readProblemDetail(response));
        setContract(null);
        return;
      }
      setContract((await response.json()) as Contract);
      // P34: the arbitrated fusions of the session (human-only, append-only).
      const fusions = await client.request("/v1/sessions/" + sessionId + "/convergences");
      if (fusions.status === 200) {
        setConvergences((await fusions.json()) as ConvergenceList);
      }
    } catch (loadFailure) {
      setLoadError(loadFailure instanceof Error ? loadFailure.message : String(loadFailure));
      setContract(null);
    } finally {
      setLoading(false);
    }
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

  /* P34 (issue #34): the human fusion arbitration - accept posts the fusion
     to the API (the covered question is never solicited again, the revision
     propagates to the surviving card and the journal), refuse is a local
     dismissal: automatic fusion is forbidden, so refusing never calls the
     API. The history stays append-only. */
  const fuse = async (question: Question) => {
    setFusionMessage(null);
    const hint = question.convergence_hint;
    if (hint === undefined || hint === null) return;
    const response = await client.request("/v1/sessions/" + sessionId + "/convergences", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question_id: question.question_id, proposition_id: hint }),
    });
    if (response.status !== 200 && response.status !== 201) {
      let detail = "";
      try {
        detail = ((await response.json()) as { detail?: string }).detail ?? "";
      } catch {
        detail = await response.text();
      }
      setFusionMessage(t("questions.fusionError", { status: response.status, detail }));
      return;
    }
    setFusionMessage(t("questions.fusionDone"));
    await loadQuestions();
  };

  const refuseFusion = (questionId: string) => {
    setRefusedIds((previous) => new Set(previous).add(questionId));
    setFusionMessage(t("questions.fusionRefused"));
  };

  /* P78: the filter/sort pipeline is presentation-only. Counters are computed
     on the full contract (shown/total, verified, controversial). */
  const allQuestions = contract?.questions ?? [];
  const uniqueValues = (pick: (question: Question) => string) =>
    [...new Set(allQuestions.map(pick))].sort((a, b) => a.localeCompare(b));
  const visibleQuestions = allQuestions
    .filter((question) => query.trim() === "" || question.formula.includes(query.trim()))
    .filter((question) => familyFilter === "" || question.family === familyFilter)
    .filter((question) => triageFilter === "" || question.triage === triageFilter)
    .filter((question) => statusFilter === "" || question.status === statusFilter)
    .sort((a, b) => {
      const left = sortKey === "id" ? a.question_id : sortKey === "family" ? a.family : a.triage;
      const right = sortKey === "id" ? b.question_id : sortKey === "family" ? b.family : b.triage;
      return left.localeCompare(right);
    });
  const verifiedCount = allQuestions.filter(
    (question) => question.cross_status === "verified_by_n",
  ).length;
  const controversialCount = allQuestions.filter(
    (question) => question.cross_status === "controversial",
  ).length;

  return (
    <section id="step-questions" hidden={hidden} aria-labelledby="h-questions">
      <h2 id="h-questions">{t("questions.title")}</h2>
      <p>{t("questions.intro")}</p>
      {loading ? (
        <p className="message message-info">
          {/* P75: the label comes from the LLM layer while a real call runs. */}
          <LlmBusy client={client} active={loading} fallbackLabel={t("questions.loading")} />
        </p>
      ) : null}
      {loadError !== null ? (
        <p className="message message-error" role="alert">
          {loadError}
        </p>
      ) : null}
      {!loading && loadError === null && (contract === null || contract.questions.length === 0) ? (
        <p className="message message-info">{t("questions.empty")}</p>
      ) : null}
      {contract !== null && contract.questions.length > 0 ? (
        <div className="filter-bar">
          <label htmlFor="question-search">{t("questions.filter.searchLabel")}</label>
          <input
            id="question-search"
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          <label htmlFor="filter-family">{t("questions.filter.family")}</label>
          <select
            id="filter-family"
            value={familyFilter}
            onChange={(event) => setFamilyFilter(event.target.value)}
          >
            <option value="">{t("questions.filter.all")}</option>
            {uniqueValues((question) => question.family).map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
          <label htmlFor="filter-triage">{t("questions.filter.triage")}</label>
          <select
            id="filter-triage"
            value={triageFilter}
            onChange={(event) => setTriageFilter(event.target.value)}
          >
            <option value="">{t("questions.filter.all")}</option>
            {uniqueValues((question) => question.triage).map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
          <label htmlFor="filter-status">{t("questions.filter.status")}</label>
          <select
            id="filter-status"
            value={statusFilter}
            onChange={(event) => setStatusFilter(event.target.value)}
          >
            <option value="">{t("questions.filter.all")}</option>
            {uniqueValues((question) => question.status).map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
          <label htmlFor="filter-sort">{t("questions.filter.sort")}</label>
          <select
            id="filter-sort"
            value={sortKey}
            onChange={(event) =>
              setSortKey(event.target.value as "id" | "family" | "triage")
            }
          >
            <option value="id">{t("questions.filter.sortId")}</option>
            <option value="family">{t("questions.filter.sortFamily")}</option>
            <option value="triage">{t("questions.filter.sortTriage")}</option>
          </select>
          <p className="filter-counters" role="status">
            {t("questions.counters", {
              shown: visibleQuestions.length,
              total: allQuestions.length,
              verified: verifiedCount,
              controversial: controversialCount,
            })}
          </p>
        </div>
      ) : null}
      <ul id="question-cards">
        {visibleQuestions.map((question) => (
          <li
            className={
              "question-card" + (question.covered_by !== undefined && question.covered_by !== null ? " covered" : "")
            }
            key={question.question_id}
          >
            <p className="question-formula">{question.formula}</p>
            <p className="badge-row">
              <span className="badge badge-family">{question.family}</span>
              <span className="badge badge-triage">{question.triage}</span>
              <span className="badge badge-status">{question.status}</span>
              {question.cross_status === "verified_by_n" ? (
                <span className="badge badge-verified">
                  {t("questions.badge.verified", { count: question.verified_by ?? 0 })}
                </span>
              ) : null}
              {question.cross_status === "controversial" ? (
                <span className="badge badge-controversial">
                  {t("questions.badge.controversial")}
                </span>
              ) : null}
            </p>
            <p className="status">{t("questions.status", { status: question.status })}</p>
            <CrossStatus question={question} />
            {question.covered_by !== undefined && question.covered_by !== null ? (
              <p className="convergence covered covered-notice">
                {t("questions.coveredBy", { survivor: question.covered_by })}
              </p>
            ) : null}
            {question.convergence_hint !== undefined &&
            question.convergence_hint !== null &&
            !refusedIds.has(question.question_id) ? (
              <div className="convergence hint">
                <p>{t("questions.convergenceHint", { hint: question.convergence_hint })}</p>
                <button type="button" className="btn" onClick={() => void fuse(question)}>
                  {t("questions.fuse")}
                </button>
                <button
                  type="button"
                  className="btn"
                  onClick={() => refuseFusion(question.question_id)}
                >
                  {t("questions.refuseFusion")}
                </button>
              </div>
            ) : null}
            <div className="decisions">
              {DECISIONS.map((decision) => (
                <button
                  key={decision.id}
                  type="button"
                  className="btn"
                  onClick={() => void decide(question.question_id, decision.id)}
                >
                  {t("questions.decisions." + decision.labelKey)}
                </button>
              ))}
              <button
                type="button"
                className="btn"
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
      {fusionMessage !== null ? <p className="fusion-message">{fusionMessage}</p> : null}
      <section className="convergences-history" aria-labelledby="h-convergences">
        <h3 id="h-convergences">{t("questions.convergencesTitle")}</h3>
        {convergences !== null && convergences.convergences.length > 0 ? (
          <ul>
            {convergences.convergences.map((record) => (
              <li key={record.question_id + "->" + record.proposition_id}>
                {t("questions.convergenceLine", {
                  questionId: record.question_id,
                  propositionId: record.proposition_id,
                })}
              </li>
            ))}
          </ul>
        ) : (
          <p>{t("questions.convergencesEmpty")}</p>
        )}
      </section>
    </section>
  );
}
