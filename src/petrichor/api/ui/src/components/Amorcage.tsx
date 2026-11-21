import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import type { ApiClient } from "../api";
import type {
  CandidateList,
  CatalogEntry,
  CatalogPage,
  ImportedModel,
  ModelProvenance,
  Profile,
  ProfileEntry,
} from "../types";

const ACCEPTED_EXTENSIONS = [".pnml", ".xml", ".zip", ".tgz", ".tar.gz", ".tar"];

const FACET_KEYS = ["family", "formalism", "edition", "source", "size"] as const;
type FacetKey = (typeof FACET_KEYS)[number];

/* Human-readable size with a translated unit (chrome string, issue #163):
   fr unit "o" -> "o"/"Ko"/"Mo", en unit "B" -> "B"/"KB"/"MB". */
function formatSize(bytes: number, unit: string): string {
  if (bytes < 1024) return `${bytes} ${unit}`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} K${unit}`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} M${unit}`;
}

/* Content-Type sent with the upload, derived from the extension (P70: the
   server accepts PNML, zip, gzip and tar archives under /v1/models). */
function contentTypeFor(fileName: string): string {
  const lower = fileName.toLowerCase();
  if (lower.endsWith(".zip")) return "application/zip";
  if (lower.endsWith(".tgz") || lower.endsWith(".tar.gz")) return "application/gzip";
  if (lower.endsWith(".tar")) return "application/x-tar";
  return "application/pnml+xml";
}

/* A pending import awaiting a candidate choice after a 409: the body is kept
   client-side and re-POSTed with ?candidate= once the user chooses. */
type PendingImport =
  | { kind: "file"; body: string | Blob; contentType: string }
  | { kind: "url"; url: string }
  | { kind: "catalog"; name: string };

interface AmorcageProps {
  hidden: boolean;
  client: ApiClient;
  sessionId: string;
  onSessionCreated: (sessionId: string) => void;
  reloadToken: number;
  resumeError: string | null;
}

/* Step 1 - import the PNML model (file, catalog name or URL), create the
   session, arbitrate the proposed semantic profile. Server state is the
   source of truth: the profile view is rebuilt from GET /profile after every
   arbitration. Server-produced values (entry ids, roles, confidences, error
   messages) are rendered verbatim and never translated (spec §9 v1.3). */
export default function Amorcage({
  hidden,
  client,
  sessionId,
  onSessionCreated,
  reloadToken,
  resumeError,
}: AmorcageProps) {
  const { t } = useTranslation();
  const [profile, setProfile] = useState<Profile | null>(null);
  const [profileMissing, setProfileMissing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [importing, setImporting] = useState(false);
  const [createdId, setCreatedId] = useState<string | null>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [importedModel, setImportedModel] = useState<{
    id: string;
    known: boolean;
    provenance: ModelProvenance | null;
  } | null>(null);

  // P71 (issue #166, spec §21): catalog search panel state.
  const [catalogQuery, setCatalogQuery] = useState("");
  const [catalogFacets, setCatalogFacets] = useState<Record<FacetKey, string>>({
    family: "",
    formalism: "",
    edition: "",
    source: "",
    size: "",
  });
  const [catalogPage, setCatalogPage] = useState<CatalogPage | null>(null);
  const [catalogLoading, setCatalogLoading] = useState(false);
  const [catalogSearched, setCatalogSearched] = useState(false);
  const [catalogName, setCatalogName] = useState("");
  const [importUrl, setImportUrl] = useState("");
  const [candidates, setCandidates] = useState<CandidateList | null>(null);
  const [pendingImport, setPendingImport] = useState<PendingImport | null>(null);

  const loadProfile = useCallback(async () => {
    if (!sessionId) return;
    const response = await client.request("/v1/sessions/" + sessionId + "/profile");
    if (response.status === 404) {
      setProfile(null);
      setProfileMissing(true);
      return;
    }
    setProfileMissing(false);
    setProfile((await response.json()) as Profile);
  }, [client, sessionId]);

  useEffect(() => {
    void loadProfile().catch((loadError: unknown) =>
      setError(loadError instanceof Error ? loadError.message : String(loadError)),
    );
  }, [loadProfile, reloadToken]);

  const hasAcceptedExtension = (file: File): boolean =>
    ACCEPTED_EXTENSIONS.some((extension) => file.name.toLowerCase().endsWith(extension));

  const searchCatalog = useCallback(
    async (overrides: Partial<Record<FacetKey, string>> = {}, query?: string) => {
      setCatalogLoading(true);
      setError(null);
      try {
        const params: Record<string, string> = {};
        const merged = { ...catalogFacets, ...overrides };
        const effectiveQuery = query ?? catalogQuery;
        // P76: the backend contract reads `search`, never `q`.
        if (effectiveQuery.trim() !== "") params.search = effectiveQuery.trim();
        for (const key of FACET_KEYS) {
          if (merged[key] !== "") params[key] = merged[key];
        }
        const result = await client.searchCatalog(params);
        if (result.status !== 200 || result.page === undefined) {
          setCatalogPage(null);
          setError(
            result.detail ?? t("amorcage.catalogUnavailable", { status: result.status }),
          );
          return;
        }
        setCatalogPage(result.page);
        setCatalogSearched(true);
      } finally {
        setCatalogLoading(false);
      }
    },
    [client, catalogFacets, catalogQuery, t],
  );

  /* Shared tail of every import path: record the confirmation (model id +
     provenance) and create the session. P68 flow unchanged. */
  const finishImport = async (model: ImportedModel, known: boolean) => {
    setImportedModel({
      id: model.id,
      known,
      provenance: model.metadata?.provenance ?? null,
    });
    const created = await client.postJson<{ session_id: string }>("/v1/sessions", {
      model_id: model.id,
    });
    // P76: an empty session id is an explicit failure, never a silent one.
    if (typeof created.session_id !== "string" || created.session_id === "") {
      throw new Error(t("amorcage.sessionCreateFailed"));
    }
    setCreatedId(created.session_id);
    onSessionCreated(created.session_id);
  };

  const runImport = async (
    call: () => Promise<{
      status: number;
      model?: ImportedModel;
      candidates?: CandidateList;
      detail?: string;
    }>,
    pending: PendingImport,
  ) => {
    setImporting(true);
    setError(null);
    setCreatedId(null);
    setImportedModel(null);
    setCandidates(null);
    try {
      const result = await call();
      if (result.status === 409 && result.candidates !== undefined) {
        setCandidates(result.candidates);
        setPendingImport(pending);
        return;
      }
      if (result.model === undefined) {
        setError(
          result.detail ?? t("amorcage.importRefused", { status: result.status }),
        );
        return;
      }
      await finishImport(result.model, result.status === 200);
    } finally {
      setImporting(false);
    }
  };

  const importFile = (file: File) => {
    if (!hasAcceptedExtension(file)) {
      setError(t("amorcage.badExtension"));
      return;
    }
    const contentType = contentTypeFor(file.name);
    void runImport(
      async () => {
        const body: string | Blob =
          contentType === "application/pnml+xml" ? await file.text() : file;
        return client.postModel(body, contentType);
      },
      { kind: "file", body: file, contentType },
    ).catch((importError: unknown) =>
      setError(importError instanceof Error ? importError.message : String(importError)),
    );
  };

  const importFromCatalogName = (name: string) => {
    const trimmed = name.trim();
    if (trimmed === "") {
      setError(t("amorcage.noCatalogName"));
      return;
    }
    void runImport(() => client.importFromCatalog(trimmed), {
      kind: "catalog",
      name: trimmed,
    }).catch((importError: unknown) =>
      setError(importError instanceof Error ? importError.message : String(importError)),
    );
  };

  const importFromUrlValue = (url: string) => {
    const trimmed = url.trim();
    if (trimmed === "") {
      setError(t("amorcage.noUrl"));
      return;
    }
    void runImport(() => client.importFromUrl(trimmed), { kind: "url", url: trimmed }).catch(
      (importError: unknown) =>
        setError(importError instanceof Error ? importError.message : String(importError)),
    );
  };

  const chooseCandidate = (candidate: string) => {
    if (pendingImport === null) return;
    const pending = pendingImport;
    void runImport(
      () =>
        pending.kind === "file"
          ? client.postModel(pending.body, pending.contentType, candidate)
          : pending.kind === "url"
            ? client.importFromUrl(pending.url, candidate)
            : client.importFromCatalog(pending.name),
      pending,
    ).catch((importError: unknown) =>
      setError(importError instanceof Error ? importError.message : String(importError)),
    );
  };

  const arbitrate = async (entry: ProfileEntry, accepted: boolean) => {
    await client.postJson("/v1/sessions/" + sessionId + "/profile/arbitrate", {
      entry_id: entry.id,
      accepted,
      chosen_role: entry.role,
    });
    await loadProfile();
  };

  const renderProvenance = (provenance: ModelProvenance): string =>
    t("amorcage.provenanceLine", {
      source: provenance.source,
      name: provenance.name ?? provenance.catalog ?? provenance.candidate ?? provenance.url ?? "—",
      sha256: provenance.sha256 ?? provenance.archive_sha256 ?? "—",
    });

  return (
    <section id="step-amorcage" hidden={hidden} aria-labelledby="h-amorcage">
      <h2 id="h-amorcage">{t("amorcage.title")}</h2>
      <p>{t("amorcage.intro")}</p>

      <details className="catalog-panel" id="catalog-panel">
        <summary>{t("amorcage.catalogTitle")}</summary>
        <p>{t("amorcage.catalogIntro")}</p>
        <div className="catalog-search">
          <label htmlFor="catalog-search">{t("amorcage.catalogSearchLabel")}</label>
          <input
            id="catalog-search"
            type="search"
            value={catalogQuery}
            onChange={(event) => setCatalogQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") void searchCatalog();
            }}
          />
          <button
            id="catalog-search-button"
            type="button"
            className="btn"
            disabled={catalogLoading}
            onClick={() => void searchCatalog()}
          >
            {t("amorcage.catalogSearchButton")}
          </button>
        </div>
        {catalogPage !== null ? (
          <div className="catalog-facets">
            {FACET_KEYS.map((key) => (
              <label key={key} htmlFor={`facet-${key}`}>
                {t(`amorcage.facet.${key}`)}
                <select
                  id={`facet-${key}`}
                  value={catalogFacets[key]}
                  onChange={(event) => {
                    const value = event.target.value;
                    setCatalogFacets((previous) => ({ ...previous, [key]: value }));
                    void searchCatalog({ [key]: value });
                  }}
                >
                  <option value="">{t("amorcage.facetAll")}</option>
                  {Object.entries(catalogPage.facets[key]).map(([value, count]) => (
                    <option key={value} value={value}>
                      {value} ({count})
                    </option>
                  ))}
                </select>
              </label>
            ))}
          </div>
        ) : null}
        {catalogLoading ? (
          <p className="message message-info" role="status">
            {t("amorcage.catalogLoading")}
          </p>
        ) : null}
        {catalogSearched && catalogPage !== null ? (
          catalogPage.items.length === 0 ? (
            <p className="message message-info">{t("amorcage.catalogEmpty")}</p>
          ) : (
            <>
              <p className="message message-info" role="status">
                {t("amorcage.catalogTotal", { count: catalogPage.total })}
              </p>
              <ul className="catalog-results">
                {catalogPage.items.map((entry: CatalogEntry) => (
                  <li key={entry.name} className="catalog-entry">
                    <span>
                      {entry.name} — {entry.family} / {entry.formalism ?? "—"} /{" "}
                      {entry.edition ?? "—"} / {entry.source}
                    </span>
                    {entry.formalism === "COL" ? (
                      <p className="message message-warning" role="note">
                        {t("amorcage.colWarning")}{" "}
                        <button
                          type="button"
                          className="btn"
                          disabled={importing}
                          onClick={() => importFromCatalogName(entry.name.replace("-COL-", "-PT-"))}
                        >
                          {t("amorcage.ptTwin", {
                            name: entry.name.replace("-COL-", "-PT-"),
                          })}
                        </button>
                      </p>
                    ) : null}
                    <button
                      type="button"
                      className="btn btn-primary"
                      disabled={importing}
                      onClick={() => importFromCatalogName(entry.name)}
                    >
                      {t("amorcage.catalogImport")}
                    </button>
                  </li>
                ))}
              </ul>
            </>
          )
        ) : null}
        <div className="catalog-by-name">
          <label htmlFor="catalog-name">{t("amorcage.importByNameLabel")}</label>
          <input
            id="catalog-name"
            type="text"
            value={catalogName}
            onChange={(event) => setCatalogName(event.target.value)}
          />
          <button
            id="catalog-name-button"
            type="button"
            className="btn"
            disabled={importing}
            onClick={() => importFromCatalogName(catalogName)}
          >
            {t("amorcage.importByNameButton")}
          </button>
        </div>
      </details>

      <div className="url-import">
        <label htmlFor="import-url">{t("amorcage.importByUrlLabel")}</label>
        <input
          id="import-url"
          type="url"
          value={importUrl}
          onChange={(event) => setImportUrl(event.target.value)}
        />
        <button
          id="import-url-button"
          type="button"
          className="btn"
          disabled={importing}
          onClick={() => importFromUrlValue(importUrl)}
        >
          {t("amorcage.importByUrlButton")}
        </button>
      </div>

      <div
        className="import-zone"
        onDragOver={(event) => event.preventDefault()}
        onDrop={(event) => {
          event.preventDefault();
          const file = event.dataTransfer.files[0];
          if (file !== undefined) setSelectedFile(file);
        }}
      >
        <label htmlFor="pnml-file" className="import-zone-label">
          {t("amorcage.dropZone")}
        </label>
        <input
          id="pnml-file"
          type="file"
          accept={ACCEPTED_EXTENSIONS.join(",")}
          aria-label={t("amorcage.fileAria")}
          className="text-sm"
          onChange={(event) => setSelectedFile(event.target.files?.[0] ?? null)}
        />
        {selectedFile !== null ? (
          <p className="import-file-info">
            {t("amorcage.fileSelected", {
              name: selectedFile.name,
              size: formatSize(selectedFile.size, t("amorcage.sizeUnit")),
            })}
          </p>
        ) : null}
        <button
          id="import-model"
          type="button"
          className="btn btn-primary"
          disabled={importing}
          onClick={() => {
            const input = document.getElementById("pnml-file") as HTMLInputElement | null;
            const file = selectedFile ?? input?.files?.[0];
            if (!file) {
              setError(t("amorcage.noFile"));
              return;
            }
            importFile(file);
          }}
        >
          {t("amorcage.import")}
        </button>
      </div>

      {candidates !== null ? (
        <fieldset className="candidate-chooser" id="candidate-chooser">
          <legend>{t("amorcage.candidatesTitle")}</legend>
          <p>{candidates.detail}</p>
          {candidates.candidates.map((candidate) => (
            <div key={candidate.sha256} className="candidate-entry">
              <label>
                <input
                  type="radio"
                  name="candidate"
                  value={candidate.name}
                  onChange={() => chooseCandidate(candidate.name)}
                />
                {t("amorcage.candidateLine", {
                  name: candidate.name,
                  size: formatSize(candidate.size_bytes, t("amorcage.sizeUnit")),
                  formalism: candidate.formalism,
                  sha256: candidate.sha256,
                })}
              </label>
            </div>
          ))}
        </fieldset>
      ) : null}

      <div id="profile" aria-live="polite">
        {/* Typed messages (issue #162): server errors verbatim in an alert,
            loading and confirmations as status - never translated. */}
        {error !== null ? (
          <p className="message message-error" role="alert">
            {String(error)}
          </p>
        ) : null}
        {resumeError !== null ? (
          <p className="message message-error" role="alert">
            {resumeError}
          </p>
        ) : null}
        {importing ? (
          <p className="message message-info" role="status">
            {t("amorcage.importing")}
          </p>
        ) : null}
        {importedModel !== null ? (
          <div className="message message-ok" role="status">
            <p>
              {importedModel.known
                ? t("amorcage.modelKnown", { id: importedModel.id })
                : t("amorcage.modelImported", { id: importedModel.id })}
            </p>
            {importedModel.provenance !== null ? (
              <p>{renderProvenance(importedModel.provenance)}</p>
            ) : null}
          </div>
        ) : null}
        {createdId !== null ? (
          <p className="message message-ok" role="status">
            {t("amorcage.sessionCreated", { id: createdId })}
          </p>
        ) : null}
        {profileMissing ? <p className="message message-info">{t("amorcage.noProfile")}</p> : null}
        {profile?.proposed.entries.map((entry) => (
          <div className="profile-entry" key={entry.id}>
            <span>
              {entry.id} — {t("amorcage.roleProposed", { role: entry.role, conf: entry.conf })}
            </span>
            <button type="button" className="btn" onClick={() => void arbitrate(entry, true)}>
              {t("amorcage.accept")}
            </button>
            <button type="button" className="btn" onClick={() => void arbitrate(entry, false)}>
              {t("amorcage.refuse")}
            </button>
          </div>
        ))}
      </div>
    </section>
  );
}
