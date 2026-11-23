import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderAtelier } from "./render-atelier";
import { applyLanguage } from "../src/i18n";

/* Clickable proof dossiers in the contract view (issue #126, P38, spec
   §17.4): each proof is clickable and leads to its dossier pieces (formula,
   verdict, witness, costs, tool versions), controversial camps are rendered
   as-is, and the witness replays step-by-step from the archive. All of it
   is server data, rendered verbatim. */

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function textResponse(status: number, body: string): Response {
  return new Response(body, { status });
}

const CONTRACT = {
  questions: [],
  journal: [],
  proof_dossiers: [
    {
      question_id: "q1",
      formula: "AG(philo0 thinking)",
      status: "open",
      cross_status: "controversial",
      verified_by: 0,
      proofs: [
        {
          run_id: "r1",
          link: "/v1/runs/r1",
          status: "done",
          triage: "mc_costly",
          verdict: "true",
          tool_version: "lola 2.0",
          technique: "reachability",
          cost_tool_seconds: 1.25,
          witness_id: "w1",
        },
        {
          run_id: "r2",
          link: "/v1/runs/r2",
          status: "done",
          triage: "mc_costly",
          verdict: "false",
          tool_version: "its 1.4",
          technique: null,
          cost_tool_seconds: null,
          witness_id: null,
        },
      ],
      camps: ["lola 2.0 : true", "its 1.4 : false"],
    },
  ],
};

const WITNESS = {
  witness_id: "w1",
  kind: "acyclic",
  cyclic: false,
  steps: [{ index: 0, marking: ["p0"], fired: "t1" }],
  narrations: [{ step_index: 0, text: "La transition t1 s'est tirée.", provenance: "llm" }],
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

describe("dossiers de preuve cliquables (issue #126)", () => {
  it("affiche les dossiers avec pièces, camps tels quels et rejeu du témoin pas à pas", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        const headers = (init?.headers ?? {}) as Record<string, string>;
        if (url === "/v1/sessions/s1/contract" && headers.Accept?.includes("text/markdown")) {
          return textResponse(200, "# Contrat\n\n- clause");
        }
        if (url === "/v1/sessions/s1/contract" && headers.Accept?.includes("contract+json")) {
          return jsonResponse(200, CONTRACT);
        }
        if (url === "/v1/runs/r1/witness") {
          return jsonResponse(200, WITNESS);
        }
        return jsonResponse(404, { status: 404 });
      }),
    );
    renderAtelier();
    fireEvent.click(screen.getByRole("button", { name: "4. Synthèse" }));
    // The dossier is clickable (details/summary) and shows the clause pieces.
    await waitFor(() => expect(screen.getByText(/AG\(philo0 thinking\)/)).toBeInTheDocument());
    expect(screen.getByRole("link", { name: /run r1/ })).toHaveAttribute("href", "/v1/runs/r1");
    expect(screen.getByText(/outil lola 2\.0/)).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "verdict : true" })).toBeInTheDocument();
    expect(screen.getByText(/coût 1\.250 s/)).toBeInTheDocument();
    expect(screen.getByText(/témoin w1/)).toBeInTheDocument();
    // Controversial camps rendered as-is, without smoothing.
    expect(screen.getByText("lola 2.0 : true")).toBeInTheDocument();
    expect(screen.getByText("its 1.4 : false")).toBeInTheDocument();
    // Step-by-step witness replay from the archive (P32 reader reused).
    fireEvent.click(screen.getByRole("button", { name: "Rejouer le témoin pas à pas" }));
    await waitFor(() => expect(screen.getByText(/tire : t1/)).toBeInTheDocument());
    expect(screen.getByText(/La transition t1 s'est tirée\./)).toBeInTheDocument();
    expect(screen.getByText("(provenance : llm)")).toBeInTheDocument();
    // The Markdown export is unchanged.
    // P79: rendered by default; the raw source is one toggle away.
    expect(screen.getByRole("heading", { name: "Contrat" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Markdown" }));
    expect(screen.getByText(/# Contrat/)).toBeInTheDocument();
  });
});
