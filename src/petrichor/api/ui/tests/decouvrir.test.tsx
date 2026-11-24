import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../src/App";
import { applyLanguage } from "../src/i18n";
import { authMeResponse } from "./auth-me-mock";

/* Zone Découvrir publique (issue #206, contrat §1/§5) : listing du catalogue
   en lecture seule via GET /v1/catalog/models, sans aucun credential. */

const CATALOG_PAGE = {
  items: [
    {
      name: "Philosophers-PT-010",
      family: "Philosophers",
      formalism: "PT",
      edition: "2026",
      source: "pnmcc-2026",
      size_bucket: "small",
      url: "https://example.invalid/x.tgz",
      sha256: null,
    },
  ],
  total: 1,
  limit: 50,
  offset: 0,
  facets: { family: {}, formalism: {}, edition: {}, source: {}, size: {} },
};

let calls: { url: string; headers: Record<string, string> }[] = [];

function mockCatalogFetch() {
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
      return new Response(JSON.stringify(CATALOG_PAGE), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
}

beforeEach(() => {
  localStorage.clear();
  window.history.replaceState(null, "", "/");
  applyLanguage("fr");
  document.documentElement.lang = "fr";
  mockCatalogFetch();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  applyLanguage("fr");
});

function renderDecouvrir() {
  render(<App />);
  fireEvent.click(screen.getByRole("button", { name: "Découvrir" }));
}

describe("zone Découvrir publique (issue #206)", () => {
  it("charge le catalogue sans aucun header credential", async () => {
    renderDecouvrir();
    await waitFor(() => expect(screen.getByText("Philosophers-PT-010")).toBeInTheDocument());
    expect(screen.getByText("1 modèle(s) dans le catalogue.")).toBeInTheDocument();
    const catalogCalls = calls.filter((call) => call.url.startsWith("/v1/catalog/models"));
    expect(catalogCalls.length).toBeGreaterThan(0);
    for (const call of catalogCalls) {
      expect(call.headers["X-API-Key"]).toBeUndefined();
    }
  });

  it("recherche dans le catalogue avec le paramètre search", async () => {
    renderDecouvrir();
    await waitFor(() => expect(screen.getByText("Philosophers-PT-010")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Recherche dans le catalogue"), {
      target: { value: "TokenRing" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Rechercher" }));
    await waitFor(() =>
      expect(
        calls.some((call) => call.url.includes("/v1/catalog/models") && call.url.includes("search=TokenRing")),
      ).toBe(true),
    );
  });

  it("est bilingue FR/EN", async () => {
    renderDecouvrir();
    await waitFor(() => expect(screen.getByText("Philosophers-PT-010")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Langue"), { target: { value: "en" } });
    expect(screen.getByText("1 model(s) in the catalog.")).toBeInTheDocument();
    expect(screen.getByLabelText("Search the catalog")).toBeInTheDocument();
  });
});
