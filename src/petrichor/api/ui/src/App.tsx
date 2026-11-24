import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { ApiClient } from "./api";
import { applyLanguage, type Language } from "./i18n";
import { applyInitialTheme, applyTheme, initialTheme, type Theme } from "./theme";
import { fetchAuthState, logout, type AuthState } from "./authSession";
import type { Session } from "./types";
import Amorcage from "./components/Amorcage";
import AtelierGate from "./components/AtelierGate";
import Decouvrir from "./components/Decouvrir";
import Landing from "./components/Landing";
import NotFound from "./components/NotFound";
import Questions from "./components/Questions";
import Preferences from "./components/Preferences";
import SiteHeader, { ZONE_IDS, type Zone } from "./components/SiteHeader";
import Verifications from "./components/Verifications";
import Synthese from "./components/Synthese";

type Step = "amorcage" | "questions" | "verifications" | "synthese";

const STEP_IDS: Step[] = ["amorcage", "questions", "verifications", "synthese"];

const SESSION_STORAGE = "petrichor.sessionId";

/* Web v1 (issue #209): the three zones are deep-linkable client routes under
   /ui/ - the server serves the SPA shell for every extension-less path, the
   app maps the path to a zone and renders the 404 page for unknown ones. */
const UI_BASE = "/ui/";
type ZoneRoute = Zone | "notfound";

function zoneFromPath(pathname: string): ZoneRoute {
  const rest = pathname.startsWith(UI_BASE)
    ? pathname.slice(UI_BASE.length)
    : pathname.replace(/^\/+/, "");
  if (rest === "" || rest === "index.html") return "accueil";
  if ((ZONE_IDS as readonly string[]).includes(rest)) return rest as Zone;
  return "notfound";
}

function pathForZone(zone: Zone): string {
  return zone === "accueil" ? UI_BASE : `${UI_BASE}${zone}`;
}

applyInitialTheme();

export default function App() {
  const { t } = useTranslation();
  // Web v1 zones (contract §1, issue #205): the landing page is the default
  // zone and emits no authenticated call; the workshop (atelier) is mounted
  // only when its zone is active, so no API request leaves the public zones.
  const [zone, setZone] = useState<ZoneRoute>(() => zoneFromPath(window.location.pathname));
  const navigate = useCallback((next: Zone) => {
    setZone(next);
    window.history.replaceState(null, "", pathForZone(next));
  }, []);
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
  const [sessionId, setSessionId] = useState<string>(
    () => localStorage.getItem(SESSION_STORAGE) ?? "",
  );
  const [step, setStep] = useState<Step>("amorcage");
  const [reloadToken, setReloadToken] = useState(0);
  const [resumeError, setResumeError] = useState<string | null>(null);
  // Web v1 §3/§4 (issue #208): the preferences panel from the header account area.
  const [preferencesOpen, setPreferencesOpen] = useState(false);
  // Web v1 §4 (issue #209): the account menu is session-aware - /auth/me says
  // whether the petrichor-session cookie is active and who the user is.
  const [auth, setAuth] = useState<AuthState>({ status: "loading" });
  useEffect(() => {
    void fetchAuthState().then(setAuth);
  }, []);
  // a11y (contract §4): a global polite announcer narrates zone changes.
  const [announcement, setAnnouncement] = useState("");
  useEffect(() => {
    setAnnouncement(zone === "notfound" ? t("errors.notFoundTitle") : t(`zones.${zone}`));
  }, [zone, t]);

  // P76/web v1 (#207): the client is stateless - the session cookie carries
  // the authentication, so there is nothing to memoize on.
  const client = useMemo(() => new ApiClient(), []);
  const bumpReload = useCallback(() => setReloadToken((token) => token + 1), []);

  const onSessionCreated = (newSessionId: string) => {
    setSessionId(newSessionId);
    localStorage.setItem(SESSION_STORAGE, newSessionId);
    bumpReload();
  };

  const onLanguageChange = (language: Language) => {
    applyLanguage(language);
  };

  const onThemeChange = (next: Theme) => {
    setTheme(next);
    applyTheme(next);
  };

  const onLogout = useCallback(() => {
    void logout().finally(() => {
      window.location.assign(UI_BASE);
    });
  }, []);

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
      <a className="skip-link" href="#main">
        {t("app.skipLink")}
      </a>
      <SiteHeader
        zone={zone === "notfound" ? "accueil" : zone}
        theme={theme}
        auth={auth}
        onNavigate={navigate}
        onToggleTheme={toggleTheme}
        onLanguageChange={onLanguageChange}
        onOpenPreferences={() => setPreferencesOpen(true)}
        onLogout={onLogout}
      />
      <div id="live-announcer" className="sr-only" aria-live="polite">
        {announcement}
      </div>
      {preferencesOpen ? (
        <Preferences
          theme={theme}
          onThemeChange={onThemeChange}
          onLanguageChange={onLanguageChange}
          onClose={() => setPreferencesOpen(false)}
        />
      ) : null}
      <main id="main">
        {zone === "accueil" ? <Landing onNavigate={navigate} /> : null}
        {zone === "decouvrir" ? <Decouvrir /> : null}
        {zone === "notfound" ? <NotFound /> : null}
        {zone === "atelier" ? (
          auth.status === "anonymous" ? (
            <AtelierGate />
          ) : auth.status === "authenticated" ? (
            <>
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
              <div className="mt-4">
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
              </div>
            </>
          ) : null
        ) : null}
      </main>
    </>
  );
}
