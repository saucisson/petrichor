import { useCallback, useState } from "react";
import { useTranslation } from "react-i18next";
import { ApiClient } from "./api";
import { applyLanguage, SUPPORTED_LANGUAGES, type Language } from "./i18n";
import type { Session } from "./types";
import Amorcage from "./components/Amorcage";
import Questions from "./components/Questions";
import Verifications from "./components/Verifications";
import Synthese from "./components/Synthese";

type Step = "amorcage" | "questions" | "verifications" | "synthese";

const STEP_IDS: Step[] = ["amorcage", "questions", "verifications", "synthese"];

const API_KEY_STORAGE = "petrichor.apiKey";
const SESSION_STORAGE = "petrichor.sessionId";

export default function App() {
  const { t, i18n } = useTranslation();
  const [apiKey, setApiKey] = useState<string>(() => localStorage.getItem(API_KEY_STORAGE) ?? "");
  const [sessionId, setSessionId] = useState<string>(
    () => localStorage.getItem(SESSION_STORAGE) ?? "",
  );
  const [step, setStep] = useState<Step>("amorcage");
  const [reloadToken, setReloadToken] = useState(0);
  const [resumeError, setResumeError] = useState<string | null>(null);

  const client = new ApiClient(apiKey);
  const bumpReload = useCallback(() => setReloadToken((token) => token + 1), []);

  const onApiKeyChange = (value: string) => {
    setApiKey(value);
    localStorage.setItem(API_KEY_STORAGE, value);
  };

  const onSessionCreated = (newSessionId: string) => {
    setSessionId(newSessionId);
    localStorage.setItem(SESSION_STORAGE, newSessionId);
    bumpReload();
  };

  const onLanguageChange = (language: Language) => {
    applyLanguage(language);
  };

  const resumeSession = async () => {
    const trimmed = sessionId.trim();
    try {
      await client.getJson<Session>("/v1/sessions/" + trimmed);
      localStorage.setItem(SESSION_STORAGE, trimmed);
      setResumeError(null);
      bumpReload();
    } catch (error) {
      // Server-produced error message: displayed verbatim, never translated.
      setResumeError(error instanceof Error ? error.message : String(error));
    }
  };

  return (
    <>
      <header>
        <h1>{t("app.title")}</h1>
        <label htmlFor="api-key">{t("header.apiKeyLabel")}</label>
        <input
          id="api-key"
          type="password"
          autoComplete="off"
          aria-label={t("header.apiKeyAria")}
          value={apiKey}
          onChange={(event) => onApiKeyChange(event.target.value)}
        />
        <label htmlFor="session-id">{t("header.sessionLabel")}</label>
        <input
          id="session-id"
          type="text"
          autoComplete="off"
          aria-label={t("header.sessionAria")}
          value={sessionId}
          onChange={(event) => setSessionId(event.target.value)}
        />
        <button id="resume-session" type="button" onClick={() => void resumeSession()}>
          {t("header.resume")}
        </button>
        <label htmlFor="language-select">{t("app.languageLabel")}</label>
        <select
          id="language-select"
          value={i18n.language}
          onChange={(event) => onLanguageChange(event.target.value as Language)}
        >
          {SUPPORTED_LANGUAGES.map((language) => (
            <option key={language} value={language}>
              {language === "fr" ? "Français" : "English"}
            </option>
          ))}
        </select>
      </header>
      <nav aria-label={t("nav.aria")}>
        <ol id="steps">
          {STEP_IDS.map((id) => (
            <li key={id}>
              <button
                type="button"
                data-step={id}
                aria-current={step === id ? "step" : undefined}
                onClick={() => setStep(id)}
              >
                {t("nav.steps." + id)}
              </button>
            </li>
          ))}
        </ol>
      </nav>
      <main>
        <Amorcage
          hidden={step !== "amorcage"}
          client={client}
          sessionId={sessionId}
          onSessionCreated={onSessionCreated}
          reloadToken={reloadToken}
          resumeError={resumeError}
        />
        <Questions
          hidden={step !== "questions"}
          client={client}
          sessionId={sessionId}
          reloadToken={reloadToken}
        />
        <Verifications
          hidden={step !== "verifications"}
          client={client}
          sessionId={sessionId}
          reloadToken={reloadToken}
        />
        <Synthese
          hidden={step !== "synthese"}
          client={client}
          sessionId={sessionId}
          reloadToken={reloadToken}
        />
      </main>
    </>
  );
}
