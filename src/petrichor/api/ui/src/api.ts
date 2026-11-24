// Typed REST client for the /v1 API. Every mutation goes through the API and
// views are rebuilt from the responses: no verdict is ever invented here
// (spec §9, §19.4).

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

  async getJson<T>(path: string, accept?: string): Promise<T> {
    const response = await this.request(
      path,
      accept === undefined ? undefined : { headers: { Accept: accept } },
    );
    return (await response.json()) as T;
  }

  async postJson<T>(path: string, body: unknown): Promise<T> {
    const response = await this.request(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return (await response.json()) as T;
  }
}
