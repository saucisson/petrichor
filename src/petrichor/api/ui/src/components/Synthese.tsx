import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { readProblemDetail, type ApiClient } from "../api";
import type { Contract, ContractProof, ProofDossier } from "../types";
import { WitnessReader } from "./Verifications";

const CONTRACT_ACCEPT = "application/contract+json; version=1.0";

/* One clickable proof dossier (P38, issue #38, spec §17.4): the clause with
   its pieces - formula, verdict per tool, witness, costs, tool versions -
   and, on controversy, the camps exported as-is. The witness replays
   step-by-step from the archive via GET /v1/runs/{run_id}/witness (the P32
   reader). All of it is server data: rendered verbatim, never translated. */
function ProofDossierCard({
  client,
  dossier,
}: {
  client: ApiClient;
  dossier: ProofDossier;
}) {
  const { t } = useTranslation();
  const [replayRunId, setReplayRunId] = useState<string | null>(null);
  return (
    <details className="proof-dossier">
      <summary>
        {dossier.formula} — {t("questions.status", { status: dossier.status })}
      </summary>
      <ul className="proof-list">
        {dossier.proofs.map((proof: ContractProof) => (
          <li key={proof.run_id}>
            <a href={proof.link}>{t("synthese.dossiers.proof.run", { runId: proof.run_id })}</a>{" "}
            — {t("synthese.dossiers.proof.tool", { tool: proof.tool_version })} —{" "}
            {t("synthese.dossiers.proof.verdict", { verdict: proof.verdict ?? "—" })}
            {proof.technique !== null ? (
              <> — {t("synthese.dossiers.proof.technique", { technique: proof.technique })}</>
            ) : null}
            {proof.cost_tool_seconds !== null ? (
              <> — {t("synthese.dossiers.proof.cost", { cost: proof.cost_tool_seconds.toFixed(3) })}</>
            ) : null}
            {proof.witness_id !== null ? (
              <>
                {" "}— {t("synthese.dossiers.proof.witness", { witnessId: proof.witness_id })}
                <button
                  type="button"
                  className="btn"
                  onClick={() =>
                    setReplayRunId(replayRunId === proof.run_id ? null : proof.run_id)
                  }
                >
                  {replayRunId === proof.run_id
                    ? t("synthese.dossiers.proof.closeReplay")
                    : t("synthese.dossiers.proof.replay")}
                </button>
              </>
            ) : null}
            {replayRunId === proof.run_id ? (
              <WitnessReader client={client} runId={proof.run_id} />
            ) : null}
          </li>
        ))}
      </ul>
      {dossier.camps.length > 0 ? (
        <div className="controversy-camps">
          <h4>{t("synthese.dossiers.campsTitle")}</h4>
          <ul>
            {dossier.camps.map((camp) => (
              <li key={camp}>{camp}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </details>
  );
}

interface SyntheseProps {
  hidden: boolean;
  client: ApiClient;
  sessionId: string;
  reloadToken: number;
}

/* Step 4 - the session contract as Markdown (exportable) and the arbitration
   journal, both read verbatim from the server and never translated (spec §9
   v1.3): the Markdown export and the journal entries are study data. */
export default function Synthese({ hidden, client, sessionId, reloadToken }: SyntheseProps) {
  const { t } = useTranslation();
  const [markdown, setMarkdown] = useState<string | null>(null);
  const [markdownError, setMarkdownError] = useState<string | null>(null);
  const [contract, setContract] = useState<Contract | null>(null);
  // P76: loading / error are explicit states - never a silent return.
  const [loading, setLoading] = useState(false);
  const [journalError, setJournalError] = useState<string | null>(null);

  const loadContractMarkdown = useCallback(async () => {
    if (!sessionId) return;
    const response = await client.request("/v1/sessions/" + sessionId + "/contract", {
      headers: { Accept: "text/markdown" },
    });
    if (response.status !== 200) {
      // The problem detail is server-produced text: displayed verbatim.
      setMarkdownError(await readProblemDetail(response));
      setMarkdown(null);
      return;
    }
    setMarkdownError(null);
    setMarkdown(await response.text());
  }, [client, sessionId]);

  const loadJournal = useCallback(async () => {
    if (!sessionId) return;
    const response = await client.request("/v1/sessions/" + sessionId + "/contract", {
      headers: { Accept: CONTRACT_ACCEPT },
    });
    if (response.status !== 200) {
      setJournalError(await readProblemDetail(response));
      setContract(null);
      return;
    }
    setJournalError(null);
    setContract((await response.json()) as Contract);
  }, [client, sessionId]);

  useEffect(() => {
    if (hidden) return;
    setLoading(true);
    void Promise.allSettled([loadContractMarkdown(), loadJournal()]).finally(() =>
      setLoading(false),
    );
  }, [hidden, loadContractMarkdown, loadJournal, reloadToken]);

  const downloadMarkdown = () => {
    if (markdown === null) return;
    const blob = new Blob([markdown], { type: "text/markdown" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = "contrat-" + sessionId + ".md";
    link.click();
    URL.revokeObjectURL(link.href);
  };

  return (
    <section id="step-synthese" hidden={hidden} aria-labelledby="h-synthese">
      <h2 id="h-synthese">{t("synthese.title")}</h2>
      <p>{t("synthese.intro")}</p>
      {loading ? (
        <p className="message message-info" role="status">
          {t("synthese.loading")}
        </p>
      ) : null}
      {markdownError !== null ? (
        <p className="message message-error" role="alert">
          {markdownError}
        </p>
      ) : null}
      {journalError !== null ? (
        <p className="message message-error" role="alert">
          {journalError}
        </p>
      ) : null}
      <button id="export-markdown" type="button" className="btn btn-primary" onClick={downloadMarkdown}>
        {t("synthese.export")}
      </button>
      <pre id="contract-preview" aria-live="polite">
        {markdownError === null ? (markdown ?? "") : ""}
      </pre>
      <h3>{t("synthese.dossiers.title")}</h3>
      {contract === null || contract.proof_dossiers === undefined || contract.proof_dossiers.length === 0 ? (
        <p>{t("synthese.dossiers.empty")}</p>
      ) : (
        <div id="proof-dossiers">
          {contract.proof_dossiers.map((dossier) => (
            <ProofDossierCard key={dossier.question_id} client={client} dossier={dossier} />
          ))}
        </div>
      )}
      <h3>{t("synthese.journalTitle")}</h3>
      {!loading && journalError === null && (contract === null || contract.journal.length === 0) ? (
        <p className="message message-info">{t("synthese.journalEmpty")}</p>
      ) : null}
      <ul id="journal-list" aria-live="polite">
        {contract?.journal.map((entry, index) => {
          const detail =
            entry.detail !== null && typeof entry.detail === "object"
              ? Object.entries(entry.detail)
                  .map(([key, value]) => `${key}=${String(value)}`)
                  .join(", ")
              : "";
          return (
            <li key={index}>
              {entry.event}
              {detail !== "" ? ` — ${detail}` : ""}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
