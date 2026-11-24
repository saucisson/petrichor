import { test, expect } from "@playwright/test";

/* E2E de la landing publique (issue #205, contrat web v1 §1, portail atelier
   issue #209) : Accueil et Découvrir fonctionnent sans compte et n'émettent
   aucune requête authentifiée ; l'Atelier sans session affiche le portail
   « Se connecter avec auth.linard.fr » (bouton unique, retour automatique). */

test("landing publique : parcours Accueil -> Découvrir -> Atelier sans compte", async ({ page }) => {
  const authenticatedCalls: string[] = [];
  page.on("request", (request) => {
    if (request.headers()["authorization"] !== undefined || request.headers()["x-api-key"] !== undefined) {
      authenticatedCalls.push(request.url());
    }
  });

  await page.goto("/");

  // Zone Accueil par défaut : présentation + liens, aucun appel réseau.
  await expect(page.locator("#landing")).toBeVisible();
  await expect(page.locator("nav button[data-zone='accueil']")).toHaveAttribute("aria-current", "page");

  // Navigation vers Découvrir (publique, lecture seule).
  await page.locator("nav button[data-zone='decouvrir']").click();
  await expect(page.locator("#decouvrir")).toBeVisible();

  // Retour Accueil puis entrée dans l'Atelier sans compte : portail de
  // connexion (contrat §1), le wizard ne se monte pas.
  await page.locator("nav button[data-zone='accueil']").click();
  await expect(page.locator("#landing")).toBeVisible();
  await page.locator("nav button[data-zone='atelier']").click();
  await expect(page.locator("#atelier-gate")).toBeVisible();
  await expect(page.locator("#atelier-login")).toBeVisible();
  await expect(page.locator("nav button[data-step='amorcage']")).toHaveCount(0);

  // Aucune requête authentifiée n'a été émise sur les zones publiques.
  expect(authenticatedCalls).toEqual([]);
});
