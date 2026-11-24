import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../src/App";
import { applyLanguage } from "../src/i18n";
import { authMeResponse, setAuthMeAuthenticated } from "./auth-me-mock";

/* Harmonisation UI au contrat commun web v1 (issue #209, contrat §4) : menu
   compte dépendant de la session (/auth/me), skip-link, annonce aria-live,
   page d'erreur 404 et portail de l'atelier sans session, zones en deep-liens
   /ui/accueil|decouvrir|atelier. */

let calls: { url: string; method: string }[] = [];

function mockFetch() {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const authMe = authMeResponse(url);
      calls.push({ url, method: init?.method ?? "GET" });
      if (authMe !== null) return authMe;
      return new Response(JSON.stringify({}), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
}

beforeEach(() => {
  localStorage.clear();
  setAuthMeAuthenticated(true);
  applyLanguage("fr");
  document.documentElement.lang = "fr";
  window.history.replaceState(null, "", "/");
  mockFetch();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  applyLanguage("fr");
});

describe("harmonisation web v1 (issue #209)", () => {
  it("expose un skip-link vers le contenu principal et un annonceur aria-live", async () => {
    render(<App />);
    const skip = screen.getByRole("link", { name: "Aller au contenu principal" });
    expect(skip).toHaveAttribute("href", "#main");
    expect(screen.getByRole("main")).toHaveAttribute("id", "main");
    const announcer = document.getElementById("live-announcer");
    expect(announcer).toHaveAttribute("aria-live", "polite");
    await waitFor(() => expect(announcer).toHaveTextContent("Accueil"));
  });

  it("le logo du header est servi sous /ui/logos/ (base Vite, ADR-0101)", async () => {
    const { container } = render(<App />);
    await screen.findByRole("button", { name: "Se déconnecter" });
    const logo = container.querySelector("header img");
    expect(logo).not.toBeNull();
    expect(logo?.getAttribute("src")).toBe("/ui/logos/petrichor-logo-sombre.svg");
  });

  it("menu compte anonyme : seul « Se connecter », qui revient à la page courante", async () => {
    setAuthMeAuthenticated(false);
    render(<App />);
    const login = await screen.findByRole("button", { name: "Se connecter" });
    expect(screen.queryByRole("button", { name: "Préférences" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Se déconnecter" })).not.toBeInTheDocument();
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign });
    fireEvent.click(login);
    expect(assign).toHaveBeenCalledWith("/auth/login?returnTo=%2F");
  });

  it("menu compte connecté : nom d'utilisateur + Préférences + Se déconnecter (POST /auth/logout)", async () => {
    render(<App />);
    expect(await screen.findByText("alice")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Préférences" })).toBeInTheDocument();
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign });
    fireEvent.click(screen.getByRole("button", { name: "Se déconnecter" }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith("/ui/"));
    expect(calls.some((call) => call.url === "/auth/logout" && call.method === "POST")).toBe(true);
  });

  it("atelier sans session : portail avec message explicite et bouton unique de connexion", async () => {
    setAuthMeAuthenticated(false);
    window.history.replaceState(null, "", "/ui/atelier");
    render(<App />);
    const gate = await screen.findByRole("heading", { name: "L'atelier est réservé aux comptes" });
    expect(gate).toBeInTheDocument();
    // Le wizard ne se monte pas sans session.
    expect(screen.queryByRole("heading", { name: "Amorçage" })).not.toBeInTheDocument();
    const cta = screen.getByRole("button", { name: "Se connecter avec auth.linard.fr" });
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign });
    fireEvent.click(cta);
    // Retour automatique vers l'atelier après la connexion.
    expect(assign).toHaveBeenCalledWith("/auth/login?returnTo=%2Fui%2Fatelier");
  });

  it("atelier connecté : le wizard se monte (deep-lien /ui/atelier)", async () => {
    window.history.replaceState(null, "", "/ui/atelier");
    render(<App />);
    expect(await screen.findByRole("heading", { name: "Amorçage" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Atelier" })).toHaveAttribute("aria-current", "page");
  });

  it("deep-lien /ui/decouvrir : la zone Découvrir s'ouvre directement", async () => {
    setAuthMeAuthenticated(false);
    window.history.replaceState(null, "", "/ui/decouvrir");
    render(<App />);
    expect(await screen.findByRole("heading", { name: "Découvrir" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Découvrir" })).toHaveAttribute("aria-current", "page");
  });

  it("route client inconnue sous /ui/ : page 404 dans la charte", async () => {
    setAuthMeAuthenticated(false);
    window.history.replaceState(null, "", "/ui/une/route/inconnue");
    render(<App />);
    expect(await screen.findByRole("heading", { name: "Page introuvable (404)" })).toBeInTheDocument();
    // La charte reste en place : header et navigation présents.
    expect(screen.getByRole("banner")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Accueil" })).toBeInTheDocument();
  });

  it("la navigation entre zones met à jour l'URL (deep-liens partageables)", async () => {
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Découvrir" }));
    expect(window.location.pathname).toBe("/ui/decouvrir");
    fireEvent.click(screen.getByRole("button", { name: "Atelier" }));
    await screen.findByRole("heading", { name: "Amorçage" });
    expect(window.location.pathname).toBe("/ui/atelier");
    fireEvent.click(screen.getByRole("button", { name: "Accueil" }));
    expect(window.location.pathname).toBe("/ui/");
  });
});
