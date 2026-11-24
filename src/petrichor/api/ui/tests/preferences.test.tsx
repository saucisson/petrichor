import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../src/App";
import { renderAtelier } from "./render-atelier";
import { applyLanguage } from "../src/i18n";
import { MISTRAL_TOKEN_STORAGE } from "../src/mistralToken";
import { authMeResponse } from "./auth-me-mock";

/* Web v1 §3 (issue #208): the preferences panel carries the per-user Mistral
   token (password field, localStorage only, clearable), and without a token
   the IA generation stays visible but disabled with an explicit message. A
   rejected token surfaces as an explicit FR message, never swallowed. */

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

type FetchCall = { url: string; method: string; body?: string; headers: Record<string, string> };

let calls: FetchCall[] = [];

function mockFetch(routes: { match: (call: FetchCall) => boolean; respond: () => Response }[]) {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const authMe = authMeResponse(String(input));
      if (authMe !== null) {
        calls.push({ url: String(input), method: init?.method ?? "GET", headers: (init?.headers ?? {}) as Record<string, string>, body: init?.body as string | undefined } as (typeof calls)[number]);
        return authMe;
      }
      const call: FetchCall = {
        url: String(input),
        method: init?.method ?? "GET",
        body: init?.body as string | undefined,
        headers: (init?.headers ?? {}) as Record<string, string>,
      };
      calls.push(call);
      const route = routes.find((candidate) => candidate.match(call));
      if (route === undefined) return jsonResponse(404, { status: 404 });
      return route.respond();
    }),
  );
}

const CONTRACT = {
  questions: [
    {
      question_id: "q1",
      formula: "AG(philo0 thinking)",
      family: "safety",
      triage: "static_free",
      status: "open",
      cross_status: "verified_by_n",
      verified_by: 2,
      proofs: [],
    },
  ],
  journal: [],
};

const CONTRACT_ROUTE = {
  match: (call: FetchCall) => call.url === "/v1/sessions/s1/contract" && call.method === "GET",
  respond: () => jsonResponse(200, CONTRACT),
};

beforeEach(() => {
  localStorage.clear();
  window.history.replaceState(null, "", "/");
  localStorage.setItem("petrichor.sessionId", "s1");
  applyLanguage("fr");
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  applyLanguage("fr");
});

describe("panneau Préférences (issue #208)", () => {
  it("s'ouvre depuis le header, masque le token (type password) et le persiste en localStorage uniquement", async () => {
    mockFetch([]);
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "Préférences" }));
    const field = screen.getByLabelText("Token d'API Mistral") as HTMLInputElement;
    expect(field.type).toBe("password");
    fireEvent.change(field, { target: { value: "sk-abc" } });
    fireEvent.blur(field);
    expect(localStorage.getItem(MISTRAL_TOKEN_STORAGE)).toBe("sk-abc");
    // The clear button empties both the field and the storage.
    fireEvent.click(screen.getByRole("button", { name: "Effacer" }));
    expect(localStorage.getItem(MISTRAL_TOKEN_STORAGE)).toBeNull();
    expect((screen.getByLabelText("Token d'API Mistral") as HTMLInputElement).value).toBe("");
    // Close via the dedicated button.
    fireEvent.click(screen.getByRole("button", { name: "Fermer" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("le panneau expose aussi le thème et la langue", async () => {
    mockFetch([]);
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "Préférences" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByLabelText("Thème")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("Langue")).toBeInTheDocument();
  });
});

describe("gating IA sans token (issue #208)", () => {
  it("désactive l'enregistrement sans token, avec message explicite, et n'émet aucun appel de génération", async () => {
    mockFetch([CONTRACT_ROUTE]);
    await renderAtelier();
    fireEvent.click(screen.getByRole("button", { name: "2. Questions statiques" }));
    await waitFor(() => expect(screen.getByText(/AG\(philo0 thinking\)/)).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Éditer la formule" }));
    const save = screen.getByRole("button", { name: "Enregistrer" }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    expect(screen.getByText(/aucun token d'API Mistral/i)).toBeInTheDocument();
    fireEvent.click(save);
    expect(calls.filter((call) => call.url.endsWith("/formula")).length).toBe(0);
  });

  it("envoie le header x-mistral-api-token uniquement sur l'appel de génération", async () => {
    localStorage.setItem(MISTRAL_TOKEN_STORAGE, "sk-ui-42");
    mockFetch([
      CONTRACT_ROUTE,
      {
        match: (call) =>
          call.url === "/v1/sessions/s1/questions/q1/formula" && call.method === "POST",
        respond: () =>
          jsonResponse(201, {
            record: {
              session_id: "s1",
              question_id: "q1",
              old_formula: "AG(philo0 thinking)",
              new_formula: "AG(philo0 eating)",
              timestamp: 1.5,
            },
            reading: { degraded: true, note: "Aucune lecture scriptée pour cette formule." },
          }),
      },
    ]);
    await renderAtelier();
    fireEvent.click(screen.getByRole("button", { name: "2. Questions statiques" }));
    await waitFor(() => expect(screen.getByText(/AG\(philo0 thinking\)/)).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Éditer la formule" }));
    const save = screen.getByRole("button", { name: "Enregistrer" }) as HTMLButtonElement;
    expect(save.disabled).toBe(false);
    fireEvent.click(save);
    await waitFor(() =>
      expect(calls.some((call) => call.url.endsWith("/formula"))).toBe(true),
    );
    const editCall = calls.find((call) => call.url.endsWith("/formula"));
    expect(editCall?.headers["x-mistral-api-token"]).toBe("sk-ui-42");
    for (const call of calls.filter((candidate) => !candidate.url.endsWith("/formula"))) {
      expect(call.headers["x-mistral-api-token"]).toBeUndefined();
    }
  });

  it("remonte un token rejeté (502 mistral_token_invalid) en message FR explicite", async () => {
    localStorage.setItem(MISTRAL_TOKEN_STORAGE, "sk-bad");
    mockFetch([
      CONTRACT_ROUTE,
      {
        match: (call) =>
          call.url === "/v1/sessions/s1/questions/q1/formula" && call.method === "POST",
        respond: () =>
          jsonResponse(502, {
            status: 502,
            detail: "Mistral rejected the API token (upstream 401).",
            extensions: { reason: "mistral_token_invalid" },
          }),
      },
    ]);
    await renderAtelier();
    fireEvent.click(screen.getByRole("button", { name: "2. Questions statiques" }));
    await waitFor(() => expect(screen.getByText(/AG\(philo0 thinking\)/)).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Éditer la formule" }));
    fireEvent.click(screen.getByRole("button", { name: "Enregistrer" }));
    await waitFor(() =>
      expect(screen.getByText(/rejeté par Mistral \(401\)/)).toBeInTheDocument(),
    );
    // The token itself is never displayed.
    expect(screen.queryByText(/sk-bad/)).not.toBeInTheDocument();
  });

  it("remonte un token manquant (428 llm_token_required) en message FR explicite", async () => {
    localStorage.setItem(MISTRAL_TOKEN_STORAGE, "sk-ui");
    mockFetch([
      CONTRACT_ROUTE,
      {
        match: (call) =>
          call.url === "/v1/sessions/s1/questions/q1/formula" && call.method === "POST",
        respond: () =>
          jsonResponse(428, {
            status: 428,
            detail: "A Mistral API token is required for this generation.",
            extensions: { reason: "llm_token_required" },
          }),
      },
    ]);
    await renderAtelier();
    fireEvent.click(screen.getByRole("button", { name: "2. Questions statiques" }));
    await waitFor(() => expect(screen.getByText(/AG\(philo0 thinking\)/)).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Éditer la formule" }));
    fireEvent.click(screen.getByRole("button", { name: "Enregistrer" }));
    await waitFor(() =>
      expect(screen.getByText(/token d'API Mistral est requis/i)).toBeInTheDocument(),
    );
  });
});
