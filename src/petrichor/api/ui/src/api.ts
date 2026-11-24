// Typed REST client for the /v1 API. Every mutation goes through the API and
// views are rebuilt from the responses: no verdict is ever invented here
// (spec §9, §19.4).

import type { CandidateList, CatalogPage, ImportedModel, LlmCallState } from "./types";

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export class ApiClient {
  constructor(private readonly apiKey: string) {}

  private headers(extra?: Record<string, string>): Record<string, string> {
    return { "X-API-Key": this.apiKey, ...(extra ?? {}) };
  }

  async request(path: string, options?: RequestInit): Promise<Response> {
    const response = await fetch(path, {
      ...options,
      headers: this.headers(options?.headers as Record<string, string> | undefined),
    });
    if (response.status === 401) {
      throw new ApiError(401, "Clé API invalide (401).");
    }
    return response;
  }

  /* P75: the observable LLM call states, polled by the UI for the active
     waiting indicator while a real call may be in flight. */
  async getLlmCalls(): Promise<LlmCallState[]> {
    const payload = await this.getJson<{ calls: LlmCallState[] }>("/v1/llm/calls");
    return payload.calls;
  }

  async getJson<T>(path: string, accept?: string): Promise<T> {
    const response = await this.request(
      path,
      accept === undefined ? undefined : { headers: { Accept: accept } },
    );
    if (!response.ok) {
      // P76: never parse an error body as data - the problem detail is the message.
      throw new ApiError(response.status, await readProblemDetail(response));
    }
    return (await response.json()) as T;
  }

  async postJson<T>(path: string, body: unknown): Promise<T> {
    const response = await this.request(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      // P76: a failed mutation (e.g. session creation) must surface explicitly.
      throw new ApiError(response.status, await readProblemDetail(response));
    }
    return (await response.json()) as T;
  }

  // P71 (issue #166, spec §21): the three import paths. Raw responses are
  // returned so the caller can branch on the typed statuses (409 candidates,
  // 422 url_not_allowed, 503 catalog_unavailable) and show server errors verbatim.

  async searchCatalog(params: Record<string, string>): Promise<{ status: number; page?: CatalogPage; detail?: string }> {
    const query = new URLSearchParams(params).toString();
    const response = await this.request(`/v1/catalog/models${query ? "?" + query : ""}`);
    if (response.status !== 200) {
      return { status: response.status, detail: await problemDetail(response) };
    }
    return { status: 200, page: (await response.json()) as CatalogPage };
  }

  async importFromCatalog(name: string): Promise<{ status: number; model?: ImportedModel; detail?: string }> {
    const response = await this.request("/v1/catalog/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    if (response.status !== 201 && response.status !== 200) {
      return { status: response.status, detail: await problemDetail(response) };
    }
    return { status: response.status, model: (await response.json()) as ImportedModel };
  }

  async importFromUrl(
    url: string,
    candidate?: string,
  ): Promise<{ status: number; model?: ImportedModel; candidates?: CandidateList; detail?: string }> {
    const response = await this.request("/v1/models/import-url", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(candidate === undefined ? { url } : { url, candidate }),
    });
    return parseImportResponse(response);
  }

  async postModel(
    body: string | Blob,
    contentType: string,
    candidate?: string,
  ): Promise<{ status: number; model?: ImportedModel; candidates?: CandidateList; detail?: string }> {
    const response = await this.request(
      candidate === undefined ? "/v1/models" : `/v1/models?candidate=${encodeURIComponent(candidate)}`,
      { method: "POST", headers: { "Content-Type": contentType }, body },
    );
    return parseImportResponse(response);
  }
}

export async function readProblemDetail(response: Response): Promise<string> {
  // The server-produced problem detail, verbatim; falls back to the status.
  try {
    const payload = (await response.json()) as { detail?: string };
    return payload.detail ?? `HTTP ${response.status}`;
  } catch {
    return `HTTP ${response.status}`;
  }
}

async function problemDetail(response: Response): Promise<string> {
  return readProblemDetail(response);
}

async function parseImportResponse(
  response: Response,
): Promise<{ status: number; model?: ImportedModel; candidates?: CandidateList; detail?: string }> {
  if (response.status === 409) {
    return { status: 409, candidates: (await response.json()) as CandidateList };
  }
  if (response.status === 201 || response.status === 200) {
    return { status: response.status, model: (await response.json()) as ImportedModel };
  }
  return { status: response.status, detail: await problemDetail(response) };
}
