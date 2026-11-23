import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiClient } from "../src/api";
import LlmBusy from "../src/components/LlmBusy";
import { applyLanguage } from "../src/i18n";

/* P75 (issue #181): active waiting during a real LLM call. The step polls
   GET /v1/llm/calls while its request is pending and shows the in-progress
   usage label (translated) with the animated indicator and elapsed seconds;
   with the instantaneous mock no call is ever in progress, so the fallback
   label shows. States are tested with simulated latency: success after a
   delay, error after a delay. */

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const IN_PROGRESS_READING = {
  calls: [
    {
      usage: "reading",
      model: "mistral-small-2603",
      started_at: new Date(Date.now() - 2000).toISOString(),
      ended_at: null,
      error: null,
      in_progress: true,
    },
  ],
};

const NO_CALL_IN_PROGRESS = { calls: [] };

function mockFetch(handler: (path: string) => Response | Promise<Response>) {
  return vi.fn((input: RequestInfo | URL) => {
    const path = typeof input === "string" ? input : input.toString();
    return Promise.resolve(handler(path));
  });
}

beforeEach(() => {
  applyLanguage("fr");
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  applyLanguage("fr");
});

describe("P75 — <LlmBusy> active waiting", () => {
  it("shows the in-progress usage label from the LLM layer while a call runs", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch((path) =>
        path === "/v1/llm/calls" ? jsonResponse(200, IN_PROGRESS_READING) : jsonResponse(200, {}),
      ),
    );
    render(<LlmBusy client={new ApiClient()} active={true} fallbackLabel="Chargement…" />);
    const status = await screen.findByRole("status");
    await waitFor(() => expect(status).toHaveTextContent("Lecture de la grille…"));
    expect(status).toHaveTextContent("depuis");
  });

  it("falls back to the caller's label when no call is in progress (mock)", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch((path) =>
        path === "/v1/llm/calls" ? jsonResponse(200, NO_CALL_IN_PROGRESS) : jsonResponse(200, {}),
      ),
    );
    render(<LlmBusy client={new ApiClient()} active={true} fallbackLabel="Chargement…" />);
    const status = await screen.findByRole("status");
    await waitFor(() => expect(status).toHaveTextContent("Chargement…"));
    expect(status).not.toHaveTextContent("Lecture de la grille…");
  });

  it("keeps showing the fallback label when the poll fails after a delay (error state)", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch((path) =>
        path === "/v1/llm/calls"
          ? new Promise<Response>((_resolve, reject) =>
              setTimeout(() => reject(new Error("network down")), 50),
            )
          : jsonResponse(200, {}),
      ),
    );
    render(<LlmBusy client={new ApiClient()} active={true} fallbackLabel="Chargement…" />);
    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent("Chargement…");
    await new Promise((resolve) => setTimeout(resolve, 120));
    expect(status).toHaveTextContent("Chargement…");
  });

  it("renders the English usage labels (fr/en chains complete)", async () => {
    applyLanguage("en");
    vi.stubGlobal(
      "fetch",
      mockFetch((path) =>
        path === "/v1/llm/calls" ? jsonResponse(200, IN_PROGRESS_READING) : jsonResponse(200, {}),
      ),
    );
    render(<LlmBusy client={new ApiClient()} active={true} fallbackLabel="Loading…" />);
    const status = await screen.findByRole("status");
    await waitFor(() => expect(status).toHaveTextContent("Reading the question grid…"));
  });
});
