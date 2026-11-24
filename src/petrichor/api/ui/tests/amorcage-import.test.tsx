import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderAtelier } from "./render-atelier";
import { authMeResponse } from "./auth-me-mock";

/* Import zone of the Amorçage step (issue #163): explicit click-or-drop
   zone, file name and size shown before sending, .pnml/.xml validation,
   sending indicator, import confirmation with the model id (idempotent
   re-import signaled), server errors verbatim. Session creation and profile
   arbitration are unchanged (strict parity with P52-P53). */

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
      const authMe = authMeResponse(url);
      if (authMe !== null) {
        calls.push({ url, method: init?.method ?? "GET", headers: (init?.headers ?? {}) as Record<string, string> } as (typeof calls)[number]);
        return authMe;
      }
      const call = { url, method: init?.method ?? "GET", body: init?.body as string | undefined };
      calls.push(call);
      const route = routes.find((candidate) => candidate.match(call));
      if (route === undefined) return jsonResponse(404, { status: 404 });
      return route.respond();
    }),
  );
}

const PROFILE = { proposed: { entries: [{ id: "p1", role: "ressource", conf: 0.9 }] } };

const IMPORT_ROUTES = (modelStatus: number) => [
  {
    match: (call: { url: string; method: string }) => call.url === "/v1/models" && call.method === "POST",
    respond: () => jsonResponse(modelStatus, { id: "m1" }),
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
  window.history.replaceState(null, "", "/");
});
/* P77 wizard navigation helpers: the import paths live behind the source
   cards (local file / remote source -> catalog or direct URL). */
function openLocalImport() {
  fireEvent.click(screen.getByRole("button", { name: /^Fichier local/ }));
}


afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("amorçage import zone (issue #163)", () => {
  it("shows the selected file name and size before sending, then confirms the import with the model id", async () => {
    mockFetch(IMPORT_ROUTES(201));
    await renderAtelier();
    const file = new File(["<pnml/>"], "philosophes.pnml", { type: "application/pnml+xml" });
    openLocalImport();
    fireEvent.change(screen.getByLabelText("Fichier PNML"), { target: { files: [file] } });
    expect(screen.getByText(/Fichier sélectionné : philosophes\.pnml/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Importer le modèle et créer la session" }));
    await waitFor(() => expect(screen.getByText("Modèle importé : m1.")).toBeInTheDocument());
    expect(screen.getByText("Session créée : s1.")).toBeInTheDocument();
    const importCall = calls.find((call) => call.url === "/v1/models");
    expect(importCall?.method).toBe("POST");
    expect(importCall?.body).toBe("<pnml/>");
  });

  it("signals an idempotent re-import when the model is already known (200)", async () => {
    mockFetch(IMPORT_ROUTES(200));
    await renderAtelier();
    const file = new File(["<pnml/>"], "philosophes.pnml", { type: "application/pnml+xml" });
    openLocalImport();
    fireEvent.change(screen.getByLabelText("Fichier PNML"), { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: "Importer le modèle et créer la session" }));
    await waitFor(() =>
      expect(screen.getByText("Modèle déjà connu (import idempotent) : m1.")).toBeInTheDocument(),
    );
  });

  it("refuses a non .pnml/.xml extension before any request", async () => {
    mockFetch(IMPORT_ROUTES(201));
    await renderAtelier();
    const file = new File(["hello"], "notes.txt", { type: "text/plain" });
    openLocalImport();
    fireEvent.change(screen.getByLabelText("Fichier PNML"), { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: "Importer le modèle et créer la session" }));
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Extension refusée : seuls .pnml, .xml, .zip, .tgz, .tar.gz et .tar sont acceptés.",
    );
    expect(calls.find((call) => call.url === "/v1/models")).toBeUndefined();
  });

  it("requires a file before sending", async () => {
    mockFetch(IMPORT_ROUTES(201));
    await renderAtelier();
    openLocalImport();
    fireEvent.click(screen.getByRole("button", { name: "Importer le modèle et créer la session" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Aucun fichier sélectionné.");
    expect(calls.find((call) => call.url === "/v1/models")).toBeUndefined();
  });

  it("accepts a dropped file in the import zone", async () => {
    mockFetch(IMPORT_ROUTES(201));
    const { container } = await renderAtelier();
    openLocalImport();
    const zone = container.querySelector(".import-zone");
    expect(zone).not.toBeNull();
    const file = new File(["<pnml/>"], "dropped.xml", { type: "application/xml" });
    fireEvent.drop(zone as Element, { dataTransfer: { files: [file] } });
    expect(screen.getByText(/Fichier sélectionné : dropped\.xml/)).toBeInTheDocument();
  });

  it("shows server errors verbatim in an alert", async () => {
    mockFetch([
      {
        match: (call: { url: string; method: string }) => call.url === "/v1/models" && call.method === "POST",
        respond: () => jsonResponse(422, { detail: "PNML invalide : balise <net> manquante." }),
      },
    ]);
    await renderAtelier();
    const file = new File(["<pnml/>"], "broken.pnml", { type: "application/pnml+xml" });
    openLocalImport();
    fireEvent.change(screen.getByLabelText("Fichier PNML"), { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: "Importer le modèle et créer la session" }));
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(
        "PNML invalide : balise <net> manquante.",
      ),
    );
  });
});
