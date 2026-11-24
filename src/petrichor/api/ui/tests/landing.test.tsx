import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../src/App";
import { applyLanguage } from "../src/i18n";
import { authMeResponse, setAuthMeAuthenticated } from "./auth-me-mock";

/* Landing page publique "/" (issue #205, contrat §1) : servie sans session,
   aucun appel authentifié émis, navigation vers Découvrir et Atelier conforme
   aux zones du contrat, bilingue FR/EN. */

let calls: { url: string; headers: Record<string, string> }[] = [];

function mockFetchEspion() {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const authMe = authMeResponse(String(input));
      if (authMe !== null) {
        calls.push({ url: String(input), method: init?.method ?? "GET", headers: (init?.headers ?? {}) as Record<string, string>, body: init?.body as string | undefined } as (typeof calls)[number]);
        return authMe;
      }
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
  window.history.replaceState(null, "", "/");
  setAuthMeAuthenticated(true);
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
  it("sert la zone Accueil par défaut sans aucun appel de données", async () => {
    setAuthMeAuthenticated(false);
    render(<App />);
    expect(screen.getByRole("main", { name: "" })).toBeInTheDocument();
    expect(screen.getByText(/élicitation de propriétés/)).toBeInTheDocument();
    // Web v1 (#209): the only network activity is the chrome's /auth/me probe
    // for the account menu - no authenticated data call leaves the landing.
    await screen.findByRole("button", { name: "Se connecter" });
    expect(calls.map((call) => call.url)).toEqual(["/auth/me"]);
  });

  it("n'émet aucun appel authentifié sur Accueil et Découvrir", async () => {
    setAuthMeAuthenticated(false);
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Découvrir" }));
    // La zone Découvrir charge le catalogue (appel public, sans credential).
    await screen.findByText(/modèle\(s\) dans le catalogue/);
    const authenticated = calls.filter((call) => call.headers["X-API-Key"] !== undefined);
    expect(authenticated).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: "Accueil" }));
    expect(calls.filter((call) => call.headers["X-API-Key"] !== undefined)).toEqual([]);
  });

  it("navigue vers l'Atelier via la navigation du contrat (zones Accueil / Découvrir / Atelier)", async () => {
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Atelier" }));
    // The workshop mounts once the account menu has resolved: stepper + Amorçage.
    expect(await screen.findByRole("heading", { name: "Amorçage" })).toBeInTheDocument();
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
