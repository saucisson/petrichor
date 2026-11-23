import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { ApiClient } from "./api";
import { applyLanguage, type Language } from "./i18n";
import { applyInitialTheme, applyTheme, initialTheme, type Theme } from "./theme";
import type { Session } from "./types";
import Amorcage from "./components/Amorcage";
import Decouvrir from "./components/Decouvrir";
import Landing from "./components/Landing";
import Questions from "./components/Questions";
import SiteHeader, { type Zone } from "./components/SiteHeader";
import Verifications from "./components/Verifications";
import Synthese from "./components/Synthese";

type Step = "amorcage" | "questions" | "verifications" | "synthese";

const STEP_IDS: Step[] = ["amorcage", "questions", "verifications", "synthese"];

const API_KEY_STORAGE = "petrichor.apiKey";
const SESSION_STORAGE = "petrichor.sessionId";

applyInitialTheme();

export default function App() {
  const { t } = useTranslation();
  // Web v1 zones (contract §1, issue #205): the landing page is the default
  // zone and emits no authenticated call; the workshop (atelier) is mounted
  // only when its zone is active, so no API request leaves the public zones.
  const [zone, setZone] = useState<Zone>("accueil");
  // P81: dark mode toggle - persisted choice first, then system preference.
  const [theme, setTheme] = useState<Theme>(() => initialTheme());
  const toggleTheme = () => {
    const next: Theme = theme === "dark" ? "light" : "dark";
    setTheme(next);
    applyTheme(next);
  };
  // Apply the theme to <html> on mount (persisted choice, else system) and on
  // every toggle; only the toggle persists the choice.
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    document.documentElement.style.colorScheme = theme;
  }, [theme]);
  const [apiKey, setApiKey] = useState<string>(() => localStorage.getItem(API_KEY_STORAGE) ?? "");
  const [sessionId, setSessionId] = useState<string>(
    () => localStorage.getItem(SESSION_STORAGE) ?? "",
  );
  const [step, setStep] = useState<Step>("amorcage");
  const [reloadToken, setReloadToken] = useState(0);
  const [resumeError, setResumeError] = useState<string | null>(null);

  // P76: the client is memoized on the key - recreating it every render
  // invalidated every useCallback depending on it and caused a fetch loop.
  const client = useMemo(() => new ApiClient(apiKey), [apiKey]);
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

  // Guided stepper (issue #162): without an active session, every step but
  // Amorçage is disabled and the reason is displayed next to the stepper.
  const sessionActive = sessionId.trim() !== "";

  return (
    <>
      <SiteHeader
        zone={zone}
        theme={theme}
        onNavigate={setZone}
        onToggleTheme={toggleTheme}
        onLanguageChange={onLanguageChange}
      />
      {zone === "accueil" ? <Landing onNavigate={setZone} /> : null}
      {zone === "decouvrir" ? <Decouvrir /> : null}
      {zone === "atelier" ? (
        <>
          <div className="header-group atelier-controls">
            <span className="header-group-label">{t("header.groupConnection")}</span>
            <label htmlFor="api-key">{t("header.apiKeyLabel")}</label>
            <input
              id="api-key"
              type="password"
              autoComplete="off"
              aria-label={t("header.apiKeyAria")}
              value={apiKey}
              onChange={(event) => onApiKeyChange(event.target.value)}
            />
          </div>
          <div className="header-group atelier-controls">
            <span className="header-group-label">{t("header.groupSession")}</span>
            <label htmlFor="session-id">{t("header.sessionLabel")}</label>
            <input
              id="session-id"
              type="text"
              autoComplete="off"
              aria-label={t("header.sessionAria")}
              value={sessionId}
              onChange={(event) => setSessionId(event.target.value)}
            />
            <button
              id="resume-session"
              type="button"
              className="btn btn-primary"
              onClick={() => void resumeSession()}
            >
              {t("header.resume")}
            </button>
          </div>
          <nav aria-label={t("nav.aria")}>
            <ol id="steps">
              {STEP_IDS.map((id) => {
                const locked = id !== "amorcage" && !sessionActive;
                return (
                  <li key={id}>
                    <button
                      type="button"
                      className="btn"
                      data-step={id}
                      aria-current={step === id ? "step" : undefined}
                      disabled={locked}
                      onClick={() => setStep(id)}
                    >
                      {t("nav.steps." + id)}
                    </button>
                  </li>
                );
              })}
            </ol>
            {!sessionActive ? (
              <p id="nav-hint" className="nav-hint">
                {t("nav.noSession")}
              </p>
            ) : null}
          </nav>
          <main className="mt-4">
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
      ) : null}
    </>
  );
}
