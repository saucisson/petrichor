import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderAtelier } from "./render-atelier";
import { applyLanguage } from "../src/i18n";

/* Formula editing in the UI (issue #125, P37 endpoints): the edit goes
   through POST /v1/sessions/{sid}/questions/{qid}/formula, the inverse
   reading is displayed labeled provenance: llm, the journal is reloaded
   (append-only), API errors are displayed explicitly, and no verdict is
   computed client-side. */

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
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

const EDIT_RESPONSE = {
  record: {
    session_id: "s1",
    question_id: "q1",
    old_formula: "AG(philo0 thinking)",
    new_formula: "AG(philo0 eating)",
    timestamp: 1.5,
  },
  reading: {
    reading: "Toujours, philo0 mange.",
    not_says: "philo0 ne pense jamais",
    pattern: "AG",
    cross: "safety",
    provenance: "llm",
    degraded: false,
  },
};

type FetchCall = { url: string; method: string; body?: string; headers: Record<string, string> };

let calls: FetchCall[] = [];

function mockFetch(routes: { match: (call: FetchCall) => boolean; respond: () => Response }[]) {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
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

const CONTRACT_ROUTE = {
  match: (call: FetchCall) =>
    call.url === "/v1/sessions/s1/contract" && call.method === "GET",
  respond: () => jsonResponse(200, CONTRACT),
};

beforeEach(() => {
  localStorage.clear();
  localStorage.setItem("petrichor.sessionId", "s1");
  // Web v1 §3 (issue #208): the IA generation needs a Mistral token.
  localStorage.setItem("petrichor.mistralToken", "sk-ui-test");
  applyLanguage("fr");
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  applyLanguage("fr");
});

describe("formule éditable (issue #125)", () => {
  it("édite la formule via l'endpoint P37, affiche la re-lecture provenance: llm et recharge le contrat", async () => {
    mockFetch([
      CONTRACT_ROUTE,
      {
        match: (call) =>
          call.url === "/v1/sessions/s1/questions/q1/formula" && call.method === "POST",
        respond: () => jsonResponse(201, EDIT_RESPONSE),
      },
    ]);
    renderAtelier();
    fireEvent.click(screen.getByRole("button", { name: "2. Questions statiques" }));
    await waitFor(() => expect(screen.getByText(/AG\(philo0 thinking\)/)).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Éditer la formule" }));
    fireEvent.change(screen.getByLabelText("Nouvelle formule"), {
      target: { value: "AG(philo0 eating)" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Enregistrer" }));
    await waitFor(() =>
      expect(
        calls.find((call) => call.url === "/v1/sessions/s1/questions/q1/formula")?.body,
      ).toBe(JSON.stringify({ formula: "AG(philo0 eating)" })),
    );
    // Issue #208: the generation call carries the token header, the reads don't.
    const editCall = calls.find((call) => call.url === "/v1/sessions/s1/questions/q1/formula");
    expect(editCall?.headers["x-mistral-api-token"]).toBe("sk-ui-test");
    const readCalls = calls.filter((call) => call.url !== "/v1/sessions/s1/questions/q1/formula");
    expect(readCalls.length).toBeGreaterThan(0);
    for (const call of readCalls) {
      expect(call.headers["x-mistral-api-token"]).toBeUndefined();
    }
    // The inverse reading is displayed, labeled provenance: llm (server text verbatim).
    await waitFor(() => expect(screen.getByText(/Toujours, philo0 mange\./)).toBeInTheDocument());
    expect(screen.getByText("(provenance : llm)")).toBeInTheDocument();
    expect(screen.getByText(/Formule mise à jour/)).toBeInTheDocument();
    // The contract was reloaded after the edit (journal visible server-side).
    await waitFor(() =>
      expect(
        calls.filter((call) => call.url === "/v1/sessions/s1/contract").length,
      ).toBeGreaterThanOrEqual(2),
    );
  });

  it("affiche explicitement l'erreur de l'API (422) sans cacher l'échec", async () => {
    mockFetch([
      CONTRACT_ROUTE,
      {
        match: (call) =>
          call.url === "/v1/sessions/s1/questions/q1/formula" && call.method === "POST",
        respond: () =>
          jsonResponse(422, {
            type: "https://petrichor.dev/problems/invalid_formula_edit",
            status: 422,
            detail: "The formula edit body is invalid: formule vide",
          }),
      },
    ]);
    renderAtelier();
    fireEvent.click(screen.getByRole("button", { name: "2. Questions statiques" }));
    await waitFor(() => expect(screen.getByText(/AG\(philo0 thinking\)/)).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Éditer la formule" }));
    fireEvent.change(screen.getByLabelText("Nouvelle formule"), { target: { value: " " } });
    fireEvent.click(screen.getByRole("button", { name: "Enregistrer" }));
    // The server detail is displayed verbatim inside the typed error line.
    await waitFor(() =>
      expect(screen.getByText(/Édition refusée \(422\) : .*formule vide/)).toBeInTheDocument(),
    );
    expect(screen.queryByText(/Formule mise à jour/)).not.toBeInTheDocument();
  });

  it("affiche l'erreur budget (429) telle quelle", async () => {
    mockFetch([
      CONTRACT_ROUTE,
      {
        match: (call) =>
          call.url === "/v1/sessions/s1/questions/q1/formula" && call.method === "POST",
        respond: () =>
          jsonResponse(429, { status: 429, detail: "Budget MC épuisé pour cette session." }),
      },
    ]);
    renderAtelier();
    fireEvent.click(screen.getByRole("button", { name: "2. Questions statiques" }));
    await waitFor(() => expect(screen.getByText(/AG\(philo0 thinking\)/)).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Éditer la formule" }));
    fireEvent.click(screen.getByRole("button", { name: "Enregistrer" }));
    await waitFor(() =>
      expect(screen.getByText(/Édition refusée \(429\) : Budget MC épuisé/)).toBeInTheDocument(),
    );
  });
});
