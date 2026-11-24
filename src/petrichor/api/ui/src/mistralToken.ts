/* Per-user Mistral API token (web v1 contract §3, issue #208): stored in the
   browser's localStorage ONLY - never in a URL, never logged, never sent in a
   response. The ApiClient attaches it as the `x-mistral-api-token` header on
   generation-triggering calls only. */

export const MISTRAL_TOKEN_STORAGE = "petrichor.mistralToken";
export const MISTRAL_TOKEN_HEADER = "x-mistral-api-token";

export function storedMistralToken(): string {
  return localStorage.getItem(MISTRAL_TOKEN_STORAGE) ?? "";
}

export function saveMistralToken(token: string): void {
  const trimmed = token.trim();
  if (trimmed === "") {
    localStorage.removeItem(MISTRAL_TOKEN_STORAGE);
    return;
  }
  localStorage.setItem(MISTRAL_TOKEN_STORAGE, trimmed);
}

export function clearMistralToken(): void {
  localStorage.removeItem(MISTRAL_TOKEN_STORAGE);
}
