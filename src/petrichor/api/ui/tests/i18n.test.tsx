import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderAtelier } from "./render-atelier";
import { applyLanguage, LANGUAGE_STORAGE, storedLanguage } from "../src/i18n";
import { authMeResponse } from "./auth-me-mock";

/* i18n of the UI chrome (issue #124, spec §9 v1.3): French by default,
   English via the persisted selector, <html lang> synchronized, and study
   data produced by the server always rendered verbatim - never translated. */

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
  journal: [{ event: "arbitration", detail: { question_id: "q1", decision: "accept" } }],
};

let calls: { url: string; method: string; body?: string }[] = [];

function mockContractFetch() {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const authMe = authMeResponse(String(input));
      if (authMe !== null) {
        calls.push({ url: String(input), method: init?.method ?? "GET", headers: (init?.headers ?? {}) as Record<string, string>, body: init?.body as string | undefined } as (typeof calls)[number]);
        return authMe;
      }
      const headers = (init?.headers ?? {}) as Record<string, string>;
      calls.push({ url, method: init?.method ?? "GET", body: init?.body as string | undefined });
      if (url === "/v1/sessions/s1/contract" && headers.Accept?.includes("contract+json")) {
        return jsonResponse(200, CONTRACT);
      }
      if (url === "/v1/sessions/s1/arbitration-requests") {
        return jsonResponse(201, {});
      }
      return jsonResponse(404, { status: 404 });
    }),
  );
}

beforeEach(() => {
  localStorage.clear();
  window.history.replaceState(null, "", "/");
  applyLanguage("fr");
  document.documentElement.lang = "fr";
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  applyLanguage("fr");
});

describe("i18n chrome (issue #124)", () => {
  it("is French by default, with <html lang=\"fr\">", async () => {
    mockContractFetch();
    await renderAtelier();
    expect(screen.getByRole("heading", { name: "Amorçage" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reprendre la session" })).toBeInTheDocument();
    expect(document.documentElement.lang).toBe("fr");
  });

  it("switches the whole chrome to English and persists the choice with <html lang>", async () => {
    mockContractFetch();
    await renderAtelier();
    fireEvent.change(screen.getByLabelText("Langue"), { target: { value: "en" } });
    expect(screen.getByRole("heading", { name: "Setup" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Resume session" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "3. MC checks" })).toBeInTheDocument();
    expect(localStorage.getItem(LANGUAGE_STORAGE)).toBe("en");
    expect(document.documentElement.lang).toBe("en");
  });

  it("restores the persisted language on a fresh load", async () => {
    localStorage.setItem(LANGUAGE_STORAGE, "en");
    expect(storedLanguage()).toBe("en");
    // A fresh module load (as on a real page load) initializes i18n in English.
    vi.resetModules();
    const freshModule = await import("../src/i18n");
    expect(freshModule.default.language).toBe("en");
    expect(document.documentElement.lang).toBe("en");
  });

  it("falls back to French on an unknown stored value", async () => {
    localStorage.setItem(LANGUAGE_STORAGE, "de");
    mockContractFetch();
    await renderAtelier();
    expect(screen.getByRole("heading", { name: "Amorçage" })).toBeInTheDocument();
    expect(document.documentElement.lang).toBe("fr");
  });

  it("never translates server-produced study data, in either locale", async () => {
    localStorage.setItem("petrichor.sessionId", "s1");
    mockContractFetch();
    await renderAtelier();
    // English chrome active...
    fireEvent.change(screen.getByLabelText("Langue"), { target: { value: "en" } });
    fireEvent.click(screen.getByRole("button", { name: "2. Static questions" }));
    await waitFor(() => expect(screen.getByText(/AG\(philo0 thinking\)/)).toBeInTheDocument());
    // ...but the formula, the status and the cross-verification stay verbatim.
    expect(screen.getByText(/AG\(philo0 thinking\)/)).toBeInTheDocument();
    expect(screen.getByText("Status: open")).toBeInTheDocument();
    expect(screen.getByText("Verified by 2 tools.")).toBeInTheDocument();
    // The arbitration decision ids sent to the API are unchanged by the locale.
    fireEvent.click(screen.getByRole("button", { name: "That's normal" }));
    await waitFor(() =>
      expect(
        calls.find((call) => call.url === "/v1/sessions/s1/arbitration-requests")?.body,
      ).toBe(JSON.stringify({ question_id: "q1", decision: "accept" })),
    );
  });
});
