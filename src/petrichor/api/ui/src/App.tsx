import { useCallback, useState } from "react";
import { ApiClient } from "./api";
import type { Session } from "./types";
import Amorcage from "./components/Amorcage";
import Questions from "./components/Questions";
import Verifications from "./components/Verifications";
import Synthese from "./components/Synthese";

type Step = "amorcage" | "questions" | "verifications" | "synthese";

const STEPS: { id: Step; label: string }[] = [
  { id: "amorcage", label: "1. Amorçage" },
  { id: "questions", label: "2. Questions statiques" },
  { id: "verifications", label: "3. Vérifications MC" },
  { id: "synthese", label: "4. Synthèse" },
];

const API_KEY_STORAGE = "petrichor.apiKey";
const SESSION_STORAGE = "petrichor.sessionId";

export default function App() {
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

  const resumeSession = async () => {
    const trimmed = sessionId.trim();
    try {
      await client.getJson<Session>("/v1/sessions/" + trimmed);
      localStorage.setItem(SESSION_STORAGE, trimmed);
      setResumeError(null);
      bumpReload();
    } catch (error) {
      setResumeError(error instanceof Error ? error.message : String(error));
    }
  };

  return (
    <>
      <header>
        <h1>Petrichor — session d'élicitation</h1>
        <label htmlFor="api-key">Clé API</label>
        <input
          id="api-key"
          type="password"
          autoComplete="off"
          aria-label="Clé API"
          value={apiKey}
          onChange={(event) => onApiKeyChange(event.target.value)}
        />
        <label htmlFor="session-id">Session</label>
        <input
          id="session-id"
          type="text"
          autoComplete="off"
          aria-label="Identifiant de session (reprise)"
          value={sessionId}
          onChange={(event) => setSessionId(event.target.value)}
        />
        <button id="resume-session" type="button" onClick={() => void resumeSession()}>
          Reprendre la session
        </button>
      </header>
      <nav aria-label="Étapes">
        <ol id="steps">
          {STEPS.map((entry) => (
            <li key={entry.id}>
              <button
                type="button"
                data-step={entry.id}
                aria-current={step === entry.id ? "step" : undefined}
                onClick={() => setStep(entry.id)}
              >
                {entry.label}
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
