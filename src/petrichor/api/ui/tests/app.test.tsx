import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../src/App";

/* Parity tests (issue #123): the React UI reproduces the four steps of the
   vanilla UI wired to the real API - same endpoints, same chrome, same
   localStorage keys, no verdict invented client-side. */

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function textResponse(status: number, body: string): Response {
  return new Response(body, { status });
}

type FetchCall = { url: string; method: string; body?: string; accept?: string };

let calls: FetchCall[] = [];

function mockFetch(
  routes: {
    match: (call: FetchCall) => boolean;
    respond: () => Response;
  }[],
) {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      const headers = (init?.headers ?? {}) as Record<string, string>;
      const call: FetchCall = { url, method, body: init?.body as string | undefined, accept: headers.Accept };
      calls.push(call);
      const route = routes.find((candidate) => candidate.match(call));
      if (route === undefined) return jsonResponse(404, { status: 404 });
      return route.respond();
    }),
  );
}

const PROFILE = { proposed: { entries: [{ id: "p1", role: "ressource", conf: 0.9 }] } };
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
  journal: [{ event: "arbitration", detail: { question_id: "q1", decision: "accept" } }],
};
const SESSION = { session_id: "s1", model_id: "m1", run_ids: ["r1"] };
const BUDGET = { consumed_seconds: 1.5, budget_seconds: 10, remaining_seconds: 8.5 };
const RUN = {
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

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("UI v2 parity with the vanilla UI", () => {
  it("renders the four steps with the first one current (a11y: aria-current)", () => {
    mockFetch([]);
    render(<App />);
    expect(screen.getByRole("heading", { name: "Amorçage" })).toBeVisible();
    const first = screen.getByRole("button", { name: "1. Amorçage" });
    expect(first).toHaveAttribute("aria-current", "step");
    expect(screen.getByRole("button", { name: "4. Synthèse" })).not.toHaveAttribute(
      "aria-current",
    );
    expect(screen.getByLabelText("Clé API")).toBeInTheDocument();
    expect(screen.getByLabelText("Identifiant de session (reprise)")).toBeInTheDocument();
  });

  it("imports the PNML model, creates the session, then loads and arbitrates the profile", async () => {
    mockFetch([
      { match: (call) => call.url === "/v1/models" && call.method === "POST", respond: () => jsonResponse(201, { id: "m1" }) },
      { match: (call) => call.url === "/v1/sessions" && call.method === "POST", respond: () => jsonResponse(201, { session_id: "s1" }) },
      { match: (call) => call.url === "/v1/sessions/s1/profile", respond: () => jsonResponse(200, PROFILE) },
      { match: (call) => call.url === "/v1/sessions/s1/profile/arbitrate" && call.method === "POST", respond: () => jsonResponse(200, {}) },
    ]);
    render(<App />);
    const file = new File(["<pnml/>"], "model.pnml", { type: "application/pnml+xml" });
    fireEvent.change(screen.getByLabelText("Fichier PNML"), { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: "Importer le modèle et créer la session" }));
    await waitFor(() => expect(screen.getByText(/rôle proposé/)).toBeInTheDocument());
    expect(localStorage.getItem("petrichor.sessionId")).toBe("s1");
    const importCall = calls.find((call) => call.url === "/v1/models");
    expect(importCall?.method).toBe("POST");
    expect(importCall?.body).toBe("<pnml/>");
    fireEvent.click(screen.getByRole("button", { name: "Accepter" }));
    await waitFor(() =>
      expect(
        calls.find((call) => call.url === "/v1/sessions/s1/profile/arbitrate")?.body,
      ).toBe(JSON.stringify({ entry_id: "p1", accepted: true, chosen_role: "ressource" })),
    );
  });

  it("shows the question cards with cross-status and sends the arbitration decision", async () => {
    localStorage.setItem("petrichor.sessionId", "s1");
    mockFetch([
      { match: (call) => call.url === "/v1/sessions/s1/contract" && (call.accept !== undefined && call.accept.includes("contract+json")), respond: () => jsonResponse(200, CONTRACT) },
      { match: (call) => call.url === "/v1/sessions/s1/arbitration-requests" && call.method === "POST", respond: () => jsonResponse(201, {}) },
    ]);
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "2. Questions statiques" }));
    await waitFor(() => expect(screen.getByText(/AG\(philo0 thinking\)/)).toBeInTheDocument());
    expect(screen.getByText("Vérifiée par 2 outils.")).toBeInTheDocument();
    expect(
      screen.getByText(/Clause controversée — remontée à l'ingénieur/),
    ).toBeInTheDocument();
    expect(screen.getByText("lola 2.0 : verdict true")).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: "C'est normal" })[0]);
    await waitFor(() =>
      expect(
        calls.find((call) => call.url === "/v1/sessions/s1/arbitration-requests")?.body,
      ).toBe(JSON.stringify({ question_id: "q1", decision: "accept" })),
    );
  });

  it("lists the runs with the MC budget line and the witness reader (provenance: llm)", async () => {
    localStorage.setItem("petrichor.sessionId", "s1");
    mockFetch([
      { match: (call) => call.url === "/v1/sessions/s1", respond: () => jsonResponse(200, SESSION) },
      { match: (call) => call.url === "/v1/sessions/s1/budget", respond: () => jsonResponse(200, BUDGET) },
      { match: (call) => call.url === "/v1/runs/r1", respond: () => jsonResponse(200, RUN) },
      { match: (call) => call.url === "/v1/runs/r1/witness", respond: () => jsonResponse(200, WITNESS) },
    ]);
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "3. Vérifications MC" }));
    await waitFor(() => expect(screen.getByText(/Budget MC — consommé 1.500 s/)).toBeInTheDocument());
    expect(screen.getByText(/aucun report/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("true")).toBeInTheDocument());
    expect(screen.getByText(/tire : t1/)).toBeInTheDocument();
    expect(
      screen.getByText(/La transition t1 s'est tirée./),
    ).toBeInTheDocument();
    expect(screen.getByText("(provenance : llm)")).toBeInTheDocument();
  });

  it("shows the contract Markdown, the journal, and exports the Markdown", async () => {
    localStorage.setItem("petrichor.sessionId", "s1");
    const createObjectURL = vi.fn(() => "blob:fake");
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL, revokeObjectURL: vi.fn() }));
    mockFetch([
      // The Accept header picks the representation: markdown vs contract+json.
      {
        match: (call) => call.url === "/v1/sessions/s1/contract" && ((call.accept !== undefined && call.accept.includes("text/markdown")) ?? false),
        respond: () => textResponse(200, "# Contrat\n\n- clause"),
      },
      {
        match: (call) => call.url === "/v1/sessions/s1/contract" && ((call.accept !== undefined && call.accept.includes("contract+json")) ?? false),
        respond: () => jsonResponse(200, CONTRACT),
      },
    ]);
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "4. Synthèse" }));
    await waitFor(() => expect(screen.getByText(/# Contrat/)).toBeInTheDocument());
    await waitFor(() =>
      expect(screen.getByText(/arbitration — question_id=q1, decision=accept/)).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Télécharger le contrat (Markdown)" }));
    expect(createObjectURL).toHaveBeenCalledOnce();
  });

  it("resumes a session from its identifier and persists it", async () => {
    mockFetch([
      { match: (call) => call.url === "/v1/sessions/s9", respond: () => jsonResponse(200, { session_id: "s9", model_id: "m9", run_ids: [] }) },
      { match: (call) => call.url === "/v1/sessions/s9/profile", respond: () => jsonResponse(404, {}) },
    ]);
    render(<App />);
    fireEvent.change(screen.getByLabelText("Identifiant de session (reprise)"), {
      target: { value: "s9" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Reprendre la session" }));
    await waitFor(() =>
      expect(screen.getByText("Aucun profil proposé pour cette session.")).toBeInTheDocument(),
    );
    expect(localStorage.getItem("petrichor.sessionId")).toBe("s9");
  });

  it("surfaces a 401 as an explicit API-key error, never a silent failure", async () => {
    mockFetch([
      { match: (call) => call.url === "/v1/sessions/s1/profile", respond: () => jsonResponse(401, {}) },
    ]);
    localStorage.setItem("petrichor.sessionId", "s1");
    render(<App />);
    await waitFor(() => expect(screen.getByText(/Clé API invalide \(401\)/)).toBeInTheDocument());
  });

  // P76 regression: a non-200 on the contract shows an alert with the server
  // detail verbatim - never a silent blank step.
  it("shows the contract 404 detail verbatim in a role=alert banner", async () => {
    localStorage.setItem("petrichor.sessionId", "s1");
    mockFetch([
      {
        match: (call) =>
          call.url === "/v1/sessions/s1/contract" &&
          (call.accept !== undefined && call.accept.includes("contract+json")),
        respond: () => jsonResponse(404, { detail: "session inconnue: s1" }),
      },
    ]);
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "2. Questions statiques" }));
    await waitFor(() => {
      const alert = screen.getByRole("alert");
      expect(alert).toHaveTextContent("session inconnue: s1");
    });
  });

  // P76 regression: a 500 on the runs list shows an alert with the server
  // detail verbatim - never a silent blank step.
  it("shows the runs 500 detail verbatim in a role=alert banner", async () => {
    localStorage.setItem("petrichor.sessionId", "s1");
    mockFetch([
      { match: (call) => call.url === "/v1/sessions/s1", respond: () => jsonResponse(200, SESSION) },
      { match: (call) => call.url === "/v1/sessions/s1/budget", respond: () => jsonResponse(200, BUDGET) },
      { match: (call) => call.url === "/v1/runs/r1", respond: () => jsonResponse(500, { detail: "moteur indisponible" }) },
    ]);
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "3. Vérifications MC" }));
    await waitFor(() => {
      const alert = screen.getByRole("alert");
      expect(alert).toHaveTextContent("moteur indisponible");
    });
  });
});

describe("UI v3 chrome (issue #162)", () => {
  it("groups the header into visible connection / session / language groups", () => {
    mockFetch([]);
    render(<App />);
    expect(screen.getByText("Connexion")).toBeInTheDocument();
    expect(screen.getAllByText("Session").length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByText("Langue").length).toBeGreaterThanOrEqual(2);
  });

  it("locks every step but Amorçage without an active session, with the reason displayed", () => {
    mockFetch([]);
    render(<App />);
    const amorcage = screen.getByRole("button", { name: "1. Amorçage" });
    expect(amorcage).toBeEnabled();
    expect(amorcage).toHaveAttribute("aria-current", "step");
    for (const name of ["2. Questions statiques", "3. Vérifications MC", "4. Synthèse"]) {
      expect(screen.getByRole("button", { name })).toBeDisabled();
    }
    expect(screen.getByText(/Créez ou reprenez une session en Amorçage/)).toBeInTheDocument();
  });

  it("unlocks every step once a session is active and hides the reason", () => {
    localStorage.setItem("petrichor.sessionId", "s1");
    mockFetch([]);
    render(<App />);
    for (const name of ["1. Amorçage", "2. Questions statiques", "3. Vérifications MC", "4. Synthèse"]) {
      expect(screen.getByRole("button", { name })).toBeEnabled();
    }
    expect(screen.queryByText(/Créez ou reprenez une session/)).not.toBeInTheDocument();
  });

  it("types the messages: confirmation as status, server error as alert verbatim", async () => {
    mockFetch([
      { match: (call) => call.url === "/v1/models" && call.method === "POST", respond: () => jsonResponse(201, { id: "m1" }) },
      { match: (call) => call.url === "/v1/sessions" && call.method === "POST", respond: () => jsonResponse(201, { session_id: "s1" }) },
      { match: (call) => call.url === "/v1/sessions/s1/profile", respond: () => jsonResponse(200, PROFILE) },
    ]);
    render(<App />);
    const file = new File(["<pnml/>"], "model.pnml", { type: "application/pnml+xml" });
    fireEvent.change(screen.getByLabelText("Fichier PNML"), { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: "Importer le modèle et créer la session" }));
    await waitFor(() => {
      const confirmation = screen.getByText(/Session créée : s1/);
      expect(confirmation).toHaveAttribute("role", "status");
    });
  });

  it("renders a server error as a role=alert message, verbatim and never translated", async () => {
    mockFetch([
      { match: (call) => call.url === "/v1/sessions/s1/profile", respond: () => jsonResponse(401, {}) },
    ]);
    localStorage.setItem("petrichor.sessionId", "s1");
    render(<App />);
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("Clé API invalide (401)."),
    );
  });
});
