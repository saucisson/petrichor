import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../src/App";

/* P71 (issue #166, spec §21): the three import paths of the Amorçage step —
   catalog search (query + facets, COL warning with -PT- twin, import by
   name), URL import, file import incl. archives — plus the 409 candidate
   chooser and the shared confirmation (model id + provenance). All endpoints
   are mocked; server-produced texts are asserted verbatim. */

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

let calls: { url: string; method: string; body?: string }[] = [];

function mockFetch(
  routes: {
    match: (call: { url: string; method: string }) => boolean;
    respond: () => Response;
  }[],
) {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const call = { url, method: init?.method ?? "GET", body: init?.body as string | undefined };
      calls.push(call);
      const route = routes.find((candidate) => candidate.match(call));
      if (route === undefined) return jsonResponse(404, { status: 404 });
      return route.respond();
    }),
  );
}

const PROFILE = { proposed: { entries: [] } };

const CATALOG_PAGE = {
  items: [
    {
      name: "philosophes-COL-v3",
      family: "philosophes",
      formalism: "COL",
      edition: "v3",
      source: "pnrepository",
      size_bucket: "small",
      url: "https://pnrepository.lip6.fr/philosophes-COL-v3.pnml",
      sha256: "abc",
    },
    {
      name: "philosophes-PT-v3",
      family: "philosophes",
      formalism: "PT",
      edition: "v3",
      source: "pnrepository",
      size_bucket: "small",
      url: "https://pnrepository.lip6.fr/philosophes-PT-v3.pnml",
      sha256: "def",
    },
  ],
  total: 2,
  limit: 50,
  offset: 0,
  facets: {
    family: { philosophes: 2 },
    formalism: { COL: 1, PT: 1 },
    edition: { v3: 2 },
    source: { pnrepository: 2 },
    size: { small: 2 },
  },
};

const MODEL_WITH_PROVENANCE = {
  id: "m42",
  metadata: {
    provenance: { source: "catalog", catalog: "pnrepository", name: "philosophes-PT-v3", sha256: "def" },
  },
};

const BASE_ROUTES = [
  {
    match: (call: { url: string; method: string }) =>
      call.url.startsWith("/v1/catalog/models") && call.method === "GET",
    respond: () => jsonResponse(200, CATALOG_PAGE),
  },
  {
    match: (call: { url: string; method: string }) =>
      call.url === "/v1/catalog/import" && call.method === "POST",
    respond: () => jsonResponse(201, MODEL_WITH_PROVENANCE),
  },
  {
    match: (call: { url: string; method: string }) =>
      call.url === "/v1/models/import-url" && call.method === "POST",
    respond: () => jsonResponse(201, { id: "m-url", metadata: { provenance: { source: "url", url: "https://example.org/m.pnml", sha256: "u1" } } }),
  },
  {
    match: (call: { url: string; method: string }) => call.url === "/v1/sessions" && call.method === "POST",
    respond: () => jsonResponse(201, { session_id: "s1" }),
  },
  {
    match: (call: { url: string; method: string }) => call.url === "/v1/sessions/s1/profile",
    respond: () => jsonResponse(200, PROFILE),
  },
];

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("amorçage catalog panel (issue #166)", () => {
  it("searches the catalog with query and facets, then imports by name with provenance confirmation", async () => {
    mockFetch(BASE_ROUTES);
    render(<App />);
    fireEvent.change(screen.getByLabelText("Recherche dans le catalogue"), {
      target: { value: "philosophes" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Rechercher" }));
    await waitFor(() => expect(screen.getByText("2 modèle(s) dans le catalogue.")).toBeInTheDocument());
    const searchCall = calls.find((call) => call.url.startsWith("/v1/catalog/models"));
    // P76: the backend contract reads `search`, never `q`.
    expect(searchCall?.url).toContain("search=philosophes");
    expect(searchCall?.url).not.toContain("q=philosophes");
    // Facet select triggers a new search with the facet parameter.
    fireEvent.change(screen.getByLabelText("Formalisme"), { target: { value: "PT" } });
    await waitFor(() => {
      const last = calls.filter((call) => call.url.startsWith("/v1/catalog/models")).at(-1);
      expect(last?.url).toContain("formalism=PT");
    });
    // Import the PT entry from the results list.
    const importButtons = screen.getAllByRole("button", { name: "Importer ce modèle" });
    fireEvent.click(importButtons[1]);
    await waitFor(() => expect(screen.getByText("Modèle importé : m42.")).toBeInTheDocument());
    expect(
      screen.getByText("Provenance : source catalog, nom philosophes-PT-v3, sha256 def."),
    ).toBeInTheDocument();
    expect(screen.getByText("Session créée : s1.")).toBeInTheDocument();
    const importCall = calls.find((call) => call.url === "/v1/catalog/import");
    expect(JSON.parse(String(importCall?.body))).toEqual({ name: "philosophes-PT-v3" });
  });

  it("warns on COL entries and proposes the -PT- twin", async () => {
    mockFetch(BASE_ROUTES);
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Rechercher" }));
    await waitFor(() => expect(screen.getByText(/réseau symétrique non déplié/)).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Importer le jumeau philosophes-PT-v3" }));
    await waitFor(() => expect(screen.getByText("Modèle importé : m42.")).toBeInTheDocument());
    const importCall = calls.find((call) => call.url === "/v1/catalog/import");
    expect(JSON.parse(String(importCall?.body))).toEqual({ name: "philosophes-PT-v3" });
  });

  it("imports by exact catalog name from the dedicated field", async () => {
    mockFetch(BASE_ROUTES);
    render(<App />);
    fireEvent.change(screen.getByLabelText("Nom exact dans le catalogue"), {
      target: { value: "philosophes-PT-v3" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Importer depuis le catalogue" }));
    await waitFor(() => expect(screen.getByText("Modèle importé : m42.")).toBeInTheDocument());
  });

  it("shows catalog-unavailable errors verbatim when the search fails", async () => {
    mockFetch([
      {
        match: (call: { url: string; method: string }) =>
          call.url.startsWith("/v1/catalog/models") && call.method === "GET",
        respond: () => jsonResponse(503, { detail: "catalog index not ready" }),
      },
    ]);
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Rechercher" }));
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("catalog index not ready"),
    );
  });

  it("refuses an unknown catalog name with the server error verbatim", async () => {
    mockFetch([
      {
        match: (call: { url: string; method: string }) =>
          call.url === "/v1/catalog/import" && call.method === "POST",
        respond: () => jsonResponse(404, { detail: "unknown catalog model: nope" }),
      },
    ]);
    render(<App />);
    fireEvent.change(screen.getByLabelText("Nom exact dans le catalogue"), { target: { value: "nope" } });
    fireEvent.click(screen.getByRole("button", { name: "Importer depuis le catalogue" }));
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("unknown catalog model: nope"),
    );
  });
});

describe("amorçage URL import (issue #166)", () => {
  it("imports from a URL and shows the provenance", async () => {
    mockFetch(BASE_ROUTES);
    render(<App />);
    fireEvent.change(screen.getByLabelText(/URL du modèle ou de l'archive/), {
      target: { value: "https://example.org/m.pnml" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Importer depuis l'URL" }));
    await waitFor(() => expect(screen.getByText("Modèle importé : m-url.")).toBeInTheDocument());
    expect(
      screen.getByText("Provenance : source url, nom https://example.org/m.pnml, sha256 u1."),
    ).toBeInTheDocument();
    const call = calls.find((item) => item.url === "/v1/models/import-url");
    expect(JSON.parse(String(call?.body))).toEqual({ url: "https://example.org/m.pnml" });
  });

  it("shows a refused URL verbatim (422 url_not_allowed)", async () => {
    mockFetch([
      {
        match: (call: { url: string; method: string }) =>
          call.url === "/v1/models/import-url" && call.method === "POST",
        respond: () =>
          jsonResponse(422, { detail: "url host not in allowlist", extensions: { reason: "url_not_allowed" } }),
      },
    ]);
    render(<App />);
    fireEvent.change(screen.getByLabelText(/URL du modèle ou de l'archive/), {
      target: { value: "https://evil.example/x.pnml" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Importer depuis l'URL" }));
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("url host not in allowlist"),
    );
  });
});

describe("amorçage archive import and 409 candidate choice (issue #166)", () => {
  const CANDIDATES = {
    reason: "multiple_candidates",
    detail: "archive contains 2 PNML candidates",
    candidates: [
      { name: "a.pnml", size_bytes: 10, sha256: "s1", formalism: "pt" },
      { name: "b.pnml", size_bytes: 20, sha256: "s2", formalism: "symmetric" },
    ],
  };

  it("offers the candidate list on 409 and re-POSTs with the chosen candidate", async () => {
    let posted = 0;
    mockFetch([
      {
        match: (call: { url: string; method: string }) =>
          call.url.startsWith("/v1/models") && call.method === "POST",
        respond: () => {
          posted += 1;
          if (posted === 1) return jsonResponse(409, CANDIDATES);
          return jsonResponse(201, {
            id: "m-arch",
            metadata: { provenance: { source: "upload", candidate: "b.pnml", archive_sha256: "z9" } },
          });
        },
      },
      {
        match: (call: { url: string; method: string }) =>
          call.url === "/v1/sessions" && call.method === "POST",
        respond: () => jsonResponse(201, { session_id: "s2" }),
      },
      {
        match: (call: { url: string; method: string }) => call.url === "/v1/sessions/s2/profile",
        respond: () => jsonResponse(200, PROFILE),
      },
    ]);
    render(<App />);
    const file = new File(["PK"], "models.zip", { type: "application/zip" });
    fireEvent.change(screen.getByLabelText("Fichier PNML"), { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: "Importer le modèle et créer la session" }));
    await waitFor(() =>
      expect(screen.getByText("Plusieurs candidats PNML dans l'archive — choisissez :")).toBeInTheDocument(),
    );
    const firstCall = calls.find((call) => call.url === "/v1/models");
    expect(firstCall?.url).toBe("/v1/models");
    fireEvent.click(screen.getByLabelText(/b\.pnml/));
    await waitFor(() => expect(screen.getByText("Modèle importé : m-arch.")).toBeInTheDocument());
    const secondCall = calls.filter((call) => call.url.startsWith("/v1/models")).at(-1);
    expect(secondCall?.url).toBe("/v1/models?candidate=b.pnml");
    expect(
      screen.getByText("Provenance : source upload, nom b.pnml, sha256 z9."),
    ).toBeInTheDocument();
  });

  it("sends archives with the archive content-type", async () => {
    mockFetch([
      {
        match: (call: { url: string; method: string }) =>
          call.url === "/v1/models" && call.method === "POST",
        respond: () => jsonResponse(201, { id: "m-zip" }),
      },
      {
        match: (call: { url: string; method: string }) =>
          call.url === "/v1/sessions" && call.method === "POST",
        respond: () => jsonResponse(201, { session_id: "s3" }),
      },
      {
        match: (call: { url: string; method: string }) => call.url === "/v1/sessions/s3/profile",
        respond: () => jsonResponse(200, PROFILE),
      },
    ]);
    render(<App />);
    const file = new File(["PK"], "models.tgz", { type: "application/gzip" });
    fireEvent.change(screen.getByLabelText("Fichier PNML"), { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: "Importer le modèle et créer la session" }));
    await waitFor(() => expect(screen.getByText("Modèle importé : m-zip.")).toBeInTheDocument());
  });
});
