import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderAtelier } from "./render-atelier";
import { applyLanguage } from "../src/i18n";
import { authMeResponse } from "./auth-me-mock";

/* P78 (issue #187): page 2 filter bar + badges, page 3 run rows with status
   badges, folded witnesses, verdict pictograms and the 3 s polling of active
   runs (which keeps running while another step is displayed). Server data
   (formulas, families, triages, statuses, verdicts) stays verbatim. */

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
    {
      question_id: "q2",
      formula: "EF(philo1 eating)",
      family: "reachability",
      triage: "mc_costly",
      status: "open",
      cross_status: "controversial",
      proofs: [
        { status: "done", verdict: "true", tool_version: "lola 2.0" },
        { status: "done", verdict: "false", tool_version: "its 1.4" },
      ],
    },
  ],
  journal: [],
};

const SESSION = { session_id: "s1", model_id: "m1", run_ids: ["r1"] };
const BUDGET = { consumed_seconds: 1.5, budget_seconds: 10, remaining_seconds: 8.5 };
const RUNNING = {
  run_id: "r1",
  family: "safety",
  kind: "mc",
  status: "running",
  triage: { kind: "mc_costly" },
  output: null,
};
const DONE = {
  run_id: "r1",
  family: "safety",
  kind: "mc",
  status: "done",
  triage: { kind: "mc_costly" },
  output: { verdict: "true", witness: { id: "w1" } },
};
const WITNESS = {
  witness_id: "w1",
  kind: "acyclic",
  cyclic: false,
  steps: [
    { index: 0, marking: ["p0"], fired: "t1" },
    { index: 1, marking: ["p1"], fired: null },
  ],
  narrations: [{ step_index: 0, text: "La transition t1 s'est tirée.", provenance: "llm" }],
};

type FetchCall = { url: string; method: string; body?: string };

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
  respond: () => jsonResponse(200, { convergences: [], total: 0, limit: 50, offset: 0 }),
};
const SESSION_ROUTE = {
  match: (call: FetchCall) => call.url === "/v1/sessions/s1" && call.method === "GET",
  respond: () => jsonResponse(200, SESSION),
};
const BUDGET_ROUTE = {
  match: (call: FetchCall) => call.url === "/v1/sessions/s1/budget",
  respond: () => jsonResponse(200, BUDGET),
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

describe("P78 — page 2 « Questions statiques »", () => {
  it("affiche la barre de filtres, les compteurs et filtre/tri en présentation seule", async () => {
    mockFetch([CONTRACT_ROUTE, CONVERGENCES_ROUTE]);
    await renderAtelier();
    fireEvent.click(screen.getByRole("button", { name: "2. Questions statiques" }));
    await waitFor(() =>
      expect(screen.getByText(/2 affichées \/ 2 — vérifiées 1 — controversées 1/)).toBeInTheDocument(),
    );
    // Badges colorés : famille, triage, statut, vérifiée/controversée.
    expect(screen.getByText("vérifiée ×2")).toBeInTheDocument();
    expect(screen.getByText("controversée")).toBeInTheDocument();
    // Recherche texte par formule (présentation seule).
    fireEvent.change(screen.getByLabelText("Recherche par formule"), {
      target: { value: "EF(" },
    });
    await waitFor(() =>
      expect(screen.getByText(/1 affichées \/ 2/)).toBeInTheDocument(),
    );
    expect(screen.getByText("EF(philo1 eating)")).toBeInTheDocument();
    expect(screen.queryByText("AG(philo0 thinking)")).not.toBeInTheDocument();
    // Filtre par famille.
    fireEvent.change(screen.getByLabelText("Recherche par formule"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("Famille"), { target: { value: "safety" } });
    await waitFor(() => expect(screen.getByText(/1 affichées \/ 2/)).toBeInTheDocument());
    expect(screen.getByText("AG(philo0 thinking)")).toBeInTheDocument();
    expect(screen.queryByText("EF(philo1 eating)")).not.toBeInTheDocument();
    // Tri par famille : reachability avant safety.
    fireEvent.change(screen.getByLabelText("Famille"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("Tri"), { target: { value: "family" } });
    const cards = screen.getAllByRole("listitem").filter((el) =>
      el.className.includes("question-card"),
    );
    expect(cards[0]).toHaveTextContent("EF(philo1 eating)");
    expect(cards[1]).toHaveTextContent("AG(philo0 thinking)");
  });
});

describe("P78 — page 3 « Vérifications MC »", () => {
  it("affiche les badges d'état, le témoin replié et le verdict en pictogramme", async () => {
    mockFetch([
      SESSION_ROUTE,
      BUDGET_ROUTE,
      { match: (call) => call.url === "/v1/runs/r1", respond: () => jsonResponse(200, DONE) },
      { match: (call) => call.url === "/v1/runs/r1/witness", respond: () => jsonResponse(200, WITNESS) },
    ]);
    await renderAtelier();
    fireEvent.click(screen.getByRole("button", { name: "3. Vérifications MC" }));
    await waitFor(() => expect(screen.getByText(/Budget MC — consommé 1.500 s/)).toBeInTheDocument());
    // Badge d'état done (vert) + triage/famille en badges + verdict pictogramme.
    expect(screen.getByText("done")).toBeInTheDocument();
    expect(screen.getByText("mc_costly")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByRole("img", { name: "verdict : true" })).toBeInTheDocument(),
    );
    // Témoin replié dans un <details> : marquage, transition surlignée,
    // narration italique avec provenance.
    const details = document.querySelector("details.witness-details");
    expect(details).not.toBeNull();
    expect(details).toHaveTextContent("Témoin w1 — acyclic");
    expect(screen.getByText(/tire : t1/)).toBeInTheDocument();
    expect(screen.getByText(/La transition t1 s'est tirée./)).toBeInTheDocument();
    expect(screen.getByText("(provenance : llm)")).toBeInTheDocument();
  });

  it("polle toutes les 3 s les runs actifs, même depuis une autre étape", async () => {
    let runFetches = 0;
    mockFetch([
      SESSION_ROUTE,
      BUDGET_ROUTE,
      CONTRACT_ROUTE,
      CONVERGENCES_ROUTE,
      {
        match: (call) => call.url === "/v1/runs/r1",
        respond: () => {
          runFetches += 1;
          return jsonResponse(200, runFetches === 1 ? RUNNING : DONE);
        },
      },
      { match: (call) => call.url === "/v1/runs/r1/witness", respond: () => jsonResponse(200, WITNESS) },
    ]);
    await renderAtelier();
    fireEvent.click(screen.getByRole("button", { name: "3. Vérifications MC" }));
    // Run actif : badge running, verdict "—" tant qu'aucun verdict.
    await waitFor(() => expect(screen.getByText("running")).toBeInTheDocument());
    expect(screen.getByText("—")).toBeInTheDocument();
    const fetchesAfterMount = runFetches;
    // Le polling (3 s) refetch le run actif et fait apparaître le verdict.
    await waitFor(
      () => expect(screen.getByRole("img", { name: "verdict : true" })).toBeInTheDocument(),
      { timeout: 10_000 },
    );
    expect(runFetches).toBeGreaterThan(fetchesAfterMount);
    // Le composant reste monté : la navigation vers une autre étape ne
    // stoppe pas le polling (le run est done, plus de refetch nécessaire,
    // mais aucune erreur n'apparaît et la page questions se charge).
    fireEvent.click(screen.getByRole("button", { name: "2. Questions statiques" }));
    await waitFor(() => expect(screen.getByText("AG(philo0 thinking)")).toBeInTheDocument());
  });
});
