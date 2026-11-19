import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import type { ApiClient } from "../api";
import type { Contract } from "../types";

const CONTRACT_ACCEPT = "application/contract+json; version=1.0";

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
  const [markdownError, setMarkdownError] = useState<number | null>(null);
  const [contract, setContract] = useState<Contract | null>(null);

  const loadContractMarkdown = useCallback(async () => {
    if (!sessionId) return;
    const response = await client.request("/v1/sessions/" + sessionId + "/contract", {
      headers: { Accept: "text/markdown" },
    });
    if (response.status !== 200) {
      setMarkdownError(response.status);
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
    if (response.status !== 200) return;
    setContract((await response.json()) as Contract);
  }, [client, sessionId]);

  useEffect(() => {
    if (hidden) return;
    void loadContractMarkdown();
    void loadJournal();
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
      <button id="export-markdown" type="button" onClick={downloadMarkdown}>
        {t("synthese.export")}
      </button>
      <pre id="contract-preview" aria-live="polite">
        {markdownError !== null
          ? t("synthese.unavailable", { status: markdownError })
          : (markdown ?? "")}
      </pre>
      <h3>{t("synthese.journalTitle")}</h3>
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
