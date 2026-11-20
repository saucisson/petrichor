import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import type { ApiClient } from "../api";
import type { Profile, ProfileEntry } from "../types";

const ACCEPTED_EXTENSIONS = [".pnml", ".xml"];

/* Human-readable size with a translated unit (chrome string, issue #163):
   fr unit "o" -> "o"/"Ko"/"Mo", en unit "B" -> "B"/"KB"/"MB". */
function formatSize(bytes: number, unit: string): string {
  if (bytes < 1024) return `${bytes} ${unit}`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} K${unit}`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} M${unit}`;
}

interface AmorcageProps {
  hidden: boolean;
  client: ApiClient;
  sessionId: string;
  onSessionCreated: (sessionId: string) => void;
  reloadToken: number;
  resumeError: string | null;
}

/* Step 1 - import the PNML model, create the session, arbitrate the proposed
   semantic profile. Server state is the source of truth: the profile view is
   rebuilt from GET /profile after every arbitration. Server-produced values
   (entry ids, roles, confidences, error messages) are rendered verbatim and
   never translated (spec §9 v1.3). */
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
  const [importedModel, setImportedModel] = useState<{ id: string; known: boolean } | null>(null);

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

  const importModelAndCreateSession = async (file: File) => {
    if (!hasAcceptedExtension(file)) {
      setError(t("amorcage.badExtension"));
      return;
    }
    setImporting(true);
    setError(null);
    setCreatedId(null);
    setImportedModel(null);
    try {
      const text = await file.text();
      const imported = await client.request("/v1/models", {
        method: "POST",
        headers: { "Content-Type": "application/pnml+xml" },
        body: text,
      });
      // 201 = new model, 200 = idempotent re-import of a known model (P03).
      if (imported.status !== 201 && imported.status !== 200) {
        throw new Error(t("amorcage.importRefused", { status: imported.status }));
      }
      const model = (await imported.json()) as { id: string };
      setImportedModel({ id: model.id, known: imported.status === 200 });
      const created = await client.postJson<{ session_id: string }>("/v1/sessions", {
        model_id: model.id,
      });
      setCreatedId(created.session_id);
      onSessionCreated(created.session_id);
    } finally {
      setImporting(false);
    }
  };

  const arbitrate = async (entry: ProfileEntry, accepted: boolean) => {
    await client.postJson("/v1/sessions/" + sessionId + "/profile/arbitrate", {
      entry_id: entry.id,
      accepted,
      chosen_role: entry.role,
    });
    await loadProfile();
  };

  return (
    <section id="step-amorcage" hidden={hidden} aria-labelledby="h-amorcage">
      <h2 id="h-amorcage">{t("amorcage.title")}</h2>
      <p>{t("amorcage.intro")}</p>
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
          accept=".pnml,.xml"
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
            importModelAndCreateSession(file).catch((importError: unknown) =>
              setError(importError instanceof Error ? importError.message : String(importError)),
            );
          }}
        >
          {t("amorcage.import")}
        </button>
      </div>
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
          <p className="message message-ok" role="status">
            {importedModel.known
              ? t("amorcage.modelKnown", { id: importedModel.id })
              : t("amorcage.modelImported", { id: importedModel.id })}
          </p>
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
