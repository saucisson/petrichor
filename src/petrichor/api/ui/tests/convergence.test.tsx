import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../src/App";
import { applyLanguage } from "../src/i18n";

/* Convergence asymétrique affichée (issue #127, P34): les convergences
   détectées à la génération sont signalées sur la carte, l'arbitrage humain
   de fusion passe par POST /v1/sessions/{sid}/convergences (jamais de fusion
   automatique), la question couverte est signalée, et l'historique des
   fusions reste affiché append-only. Aucun verdict n'est calculé côté client. */

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
      convergence_hint: "q0",
    },
    {
      question_id: "q2",
      formula: "AG(philo1 thinking)",
      family: "safety",
      triage: "static_free",
      status: "open",
      cross_status: "verified_by_n",
      verified_by: 2,
      proofs: [],
      covered_by: "q0",
    },
  ],
  journal: [],
};

const CONVERGENCES = {
  convergences: [
    {
      session_id: "s1",
      question_id: "q2",
      proposition_id: "q0",
      timestamp: 1700000000,
    },
  ],
  total: 1,
  limit: 50,
  offset: 0,
};

type FetchCall = { url: string; method: string; body?: string };

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
      };
      calls.push(call);
      const route = routes.find((candidate) => candidate.match(call));
      if (route === undefined) return jsonResponse(404, { status: 404 });
      return route.respond();
    }),
  );
}

const CONTRACT_ROUTE = {
  match: (call: FetchCall) => call.url === "/v1/sessions/s1/contract" && call.method === "GET",
  respond: () => jsonResponse(200, CONTRACT),
};

const CONVERGENCES_ROUTE = {
  match: (call: FetchCall) => call.url === "/v1/sessions/s1/convergences" && call.method === "GET",
  respond: () => jsonResponse(200, CONVERGENCES),
};

beforeEach(() => {
  localStorage.clear();
  localStorage.setItem("petrichor.sessionId", "s1");
  applyLanguage("fr");
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  applyLanguage("fr");
});

describe("convergence asymétrique (issue #127)", () => {
  it("signale la convergence, arbitre la fusion via l'API et recharge le contrat", async () => {
    mockFetch([
      CONTRACT_ROUTE,
      CONVERGENCES_ROUTE,
      {
        match: (call) => call.url === "/v1/sessions/s1/convergences" && call.method === "POST",
        respond: () => jsonResponse(201, CONVERGENCES.convergences[0]),
      },
    ]);
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "2. Questions statiques" }));
    // The detected convergence is signaled on the card.
    await waitFor(() => expect(screen.getByText(/converge avec q0/)).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Arbitrer la fusion (accepter)" }));
    await waitFor(() =>
      expect(
        calls.find((call) => call.url === "/v1/sessions/s1/convergences" && call.method === "POST")
          ?.body,
      ).toBe(JSON.stringify({ question_id: "q1", proposition_id: "q0" })),
    );
    await waitFor(() => expect(screen.getByText(/Fusion arbitrée/)).toBeInTheDocument());
    // The contract was reloaded after the fusion (revision propagation + journal).
    await waitFor(() =>
      expect(calls.filter((call) => call.url === "/v1/sessions/s1/contract").length).toBeGreaterThanOrEqual(2),
    );
  });

  it("refuse la fusion localement sans appeler l'API (pas de fusion automatique)", async () => {
    mockFetch([CONTRACT_ROUTE, CONVERGENCES_ROUTE]);
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "2. Questions statiques" }));
    await waitFor(() => expect(screen.getByText(/converge avec q0/)).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Refuser la fusion" }));
    await waitFor(() => expect(screen.getByText(/Fusion refusée/)).toBeInTheDocument());
    expect(
      calls.find((call) => call.url === "/v1/sessions/s1/convergences" && call.method === "POST"),
    ).toBeUndefined();
  });

  it("affiche la question couverte et l'historique append-only des fusions", async () => {
    mockFetch([CONTRACT_ROUTE, CONVERGENCES_ROUTE]);
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "2. Questions statiques" }));
    await waitFor(() =>
      expect(screen.getByText(/couverte par la fusion avec q0/)).toBeInTheDocument(),
    );
    // Append-only history section, loaded from GET /convergences.
    await waitFor(() =>
      expect(screen.getByText("Fusions arbitrées (humain uniquement)")).toBeInTheDocument(),
    );
    expect(screen.getByText("q2 → couverte par q0")).toBeInTheDocument();
  });

  it("affiche explicitement l'erreur de l'API (422) sur fusion invalide", async () => {
    mockFetch([
      CONTRACT_ROUTE,
      CONVERGENCES_ROUTE,
      {
        match: (call) => call.url === "/v1/sessions/s1/convergences" && call.method === "POST",
        respond: () =>
          jsonResponse(422, {
            status: 422,
            detail: "q1 ne converge pas réellement avec q0",
          }),
      },
    ]);
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "2. Questions statiques" }));
    await waitFor(() => expect(screen.getByText(/converge avec q0/)).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Arbitrer la fusion (accepter)" }));
    await waitFor(() =>
      expect(screen.getByText(/Fusion refusée par l'API \(422\) : .*q1 ne converge pas/)).toBeInTheDocument(),
    );
    expect(screen.queryByText(/Fusion arbitrée/)).not.toBeInTheDocument();
  });
});
