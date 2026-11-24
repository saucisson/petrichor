/* Shared /auth/me mock for the vitest suites (web v1, issue #209): the App
   chrome probes the session once on mount to render the account menu. Suites
   run authenticated by default (the workshop tests need the wizard); the
   harmonisation suite flips to anonymous with setAuthMeAuthenticated(false). */

let authenticated = true;

export function setAuthMeAuthenticated(value: boolean): void {
  authenticated = value;
}

export const AUTH_ME_URL = "/auth/me";

export function authMeResponse(url: string): Response | null {
  if (url !== AUTH_ME_URL) return null;
  if (!authenticated) {
    return new Response(
      JSON.stringify({
        status: 401,
        detail: "No active session.",
        extensions: { reason: "session_required" },
      }),
      { status: 401, headers: { "Content-Type": "application/problem+json" } },
    );
  }
  return new Response(
    JSON.stringify({
      sub: "user-1",
      iss: "https://auth.test",
      preferred_username: "alice",
      name: "Alice Martin",
    }),
    { status: 200, headers: { "Content-Type": "application/json" } },
  );
}
