import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import type { ApiClient } from "../api";
import type { Profile, ProfileEntry } from "../types";

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

  const importModelAndCreateSession = async (file: File) => {
    const text = await file.text();
    const imported = await client.request("/v1/models", {
      method: "POST",
      headers: { "Content-Type": "application/pnml+xml" },
      body: text,
    });
    if (imported.status !== 201) throw new Error(t("amorcage.importRefused", { status: imported.status }));
    const model = (await imported.json()) as { id: string };
    const created = await client.postJson<{ session_id: string }>("/v1/sessions", {
      model_id: model.id,
    });
    onSessionCreated(created.session_id);
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
      <input
        id="pnml-file"
        type="file"
        accept=".pnml,.xml"
        aria-label={t("amorcage.fileAria")}
        className="text-sm"
      />
      <button
        id="import-model"
        type="button"
        className="btn btn-primary"
        onClick={() => {
          const input = document.getElementById("pnml-file") as HTMLInputElement | null;
          const file = input?.files?.[0];
          if (!file) return;
          importModelAndCreateSession(file).catch((importError: unknown) =>
            setError(importError instanceof Error ? importError.message : String(importError)),
          );
        }}
      >
        {t("amorcage.import")}
      </button>
      <div id="profile" aria-live="polite">
        {error !== null ? String(error) : null}
        {resumeError !== null ? resumeError : null}
        {profileMissing ? t("amorcage.noProfile") : null}
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
