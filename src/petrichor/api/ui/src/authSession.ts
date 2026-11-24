/* Web v1 account session (contract docs/consigne-interface-web-v1.md §2/§4,
   issues #207/#209): the browser never sees an OIDC token - the HttpOnly
   `petrichor-session` cookie carries the authentication. `/auth/me` tells the
   chrome whether a session exists and who the user is (account menu); the
   token itself stays server-side. */

export type AuthState =
  | { status: "loading" }
  | { status: "anonymous" }
  | { status: "authenticated"; userName: string };

export interface AuthMe {
  sub: string;
  iss: string;
  preferred_username?: string;
  name?: string;
}

async function readAuthMe(): Promise<AuthMe | null> {
  let response: Response;
  try {
    response = await fetch("/auth/me", { credentials: "include" });
  } catch {
    // Network-level failure: treat as anonymous, the API calls will redirect
    // to the login on their own 401 anyway (ApiClient.request).
    return null;
  }
  if (response.status === 401) return null;
  if (!response.ok) return null;
  return (await response.json()) as AuthMe;
}

export async function fetchAuthState(): Promise<AuthState> {
  const me = await readAuthMe();
  if (me === null) return { status: "anonymous" };
  const userName = me.preferred_username ?? me.name ?? me.sub;
  return { status: "authenticated", userName };
}

/* POST /auth/logout revokes the IdP tokens, destroys the server session and
   expires the cookie; the browser then lands back on the public UI. */
export async function logout(): Promise<void> {
  await fetch("/auth/logout", { method: "POST", credentials: "include" });
}

export function loginUrl(returnTo: string): string {
  return `/auth/login?returnTo=${encodeURIComponent(returnTo)}`;
}
