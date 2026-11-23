import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../src/App";
import { applyLanguage } from "../src/i18n";

/* Landing page publique "/" (issue #205, contrat §1) : servie sans session,
   aucun appel authentifié émis, navigation vers Découvrir et Atelier conforme
   aux zones du contrat, bilingue FR/EN. */

let calls: { url: string; headers: Record<string, string> }[] = [];

function mockFetchEspion() {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({
        url: String(input),
        headers: (init?.headers ?? {}) as Record<string, string>,
      });
      return new Response(JSON.stringify({}), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
}

beforeEach(() => {
  localStorage.clear();
  applyLanguage("fr");
  document.documentElement.lang = "fr";
  mockFetchEspion();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  applyLanguage("fr");
});

describe("landing publique (issue #205)", () => {
  it("sert la zone Accueil par défaut sans aucun appel réseau", () => {
    render(<App />);
    expect(screen.getByRole("main", { name: "" })).toBeInTheDocument();
    expect(screen.getByText(/élicitation de propriétés/)).toBeInTheDocument();
    expect(calls).toEqual([]);
  });

  it("n'émet aucun appel authentifié sur Accueil et Découvrir", () => {
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Découvrir" }));
    expect(screen.getByText(/lecture seule/)).toBeInTheDocument();
    expect(calls).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: "Accueil" }));
    expect(calls).toEqual([]);
  });

  it("navigue vers l'Atelier via la navigation du contrat (zones Accueil / Découvrir / Atelier)", () => {
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Atelier" }));
    // The workshop mounts: its stepper and the Amorçage heading appear.
    expect(screen.getByRole("heading", { name: "Amorçage" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Atelier" }).getAttribute("aria-current")).toBe(
      "page",
    );
  });

  it("est bilingue FR/EN avec parité des clés de la landing", () => {
    render(<App />);
    fireEvent.change(screen.getByLabelText("Langue"), { target: { value: "en" } });
    expect(screen.getByText(/property elicitation on Petri nets/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Home" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Explore" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Workshop" })).toBeInTheDocument();
    expect(document.documentElement.lang).toBe("en");
  });
});
