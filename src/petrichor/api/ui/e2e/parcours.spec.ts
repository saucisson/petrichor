import { test, expect } from "@playwright/test";
import { fileURLToPath } from "node:url";

/* E2E navigateur de la voie UI (friction F5, issue #128): the full 4-step
   parcours is driven in a real Chromium against the real API served by
   tests/test_ui_e2e.py (itself wired to the real verifypn compose engine).
   The harness plays only what the UI cannot: proposing the semantic profile
   and posting the MC checks. Everything else goes through the browser:
   amorçage, arbitrages, édition de formule, convergence, témoin, contrat,
   dossier de preuve, export Markdown, bascule de langue. Server-produced
   texts (formulas, verdicts, narrations) are asserted verbatim, never
   translated. */

const API_KEY = process.env.PETRICHOR_UI_API_KEY ?? "test-key";
const PNML_FIXTURE =
  process.env.PETRICHOR_PNML_FIXTURE ??
  fileURLToPath(new URL("../../../../tests/fixtures/philosophes3-maquette.pnml", import.meta.url));

test("parcours complet des 4 étapes + arbitrage + langue + édition + dossier + convergence", async ({
  page,
}) => {
  await page.goto("/");

  // Étape 1 - amorçage: clé API, import PNML, création de session.
  await page.locator("#api-key").fill(API_KEY);
  // P77 wizard: the local file import lives behind the "Fichier local" card.
  await page.getByRole("button", { name: "Fichier local" }).click();
  await page.locator("#pnml-file").setInputFiles(PNML_FIXTURE);
  await page.locator("#import-model").click();
  await expect(page.locator("#session-id")).not.toHaveValue("");
  // The harness proposes the semantic profile server-side as soon as the
  // session appears; the UI reads the profile on mount only, so reload until
  // the proposed entries show up (the proposal races the import).
  await expect
    .poll(
      async () => {
        const count = await page.locator(".profile-entry").count();
        if (count === 0) await page.reload();
        return count;
      },
      { timeout: 90_000, intervals: [1_000, 2_000, 5_000] },
    )
    .toBeGreaterThan(0);

  // Arbitrage du profil sémantique proposé (par le harnais côté serveur).
  const entries = page.locator(".profile-entry");
  const profileCount = await entries.count();
  expect(profileCount).toBeGreaterThan(0);
  for (let index = 0; index < profileCount; index += 1) {
    await entries.nth(index).getByRole("button", { name: "Accepter" }).click();
  }

  // Étape 2 - questions: arbitrage de chaque carte.
  await page.locator("nav button[data-step='questions']").click();
  await expect(page.locator(".question-card").first()).toBeVisible({ timeout: 60_000 });
  const cards = page.locator(".question-card");
  const cardCount = await cards.count();
  expect(cardCount).toBeGreaterThan(0);
  for (let index = 0; index < cardCount; index += 1) {
    await cards.nth(index).getByRole("button", { name: "C'est normal" }).click();
  }

  // Édition de formule (P37) sur la première carte + re-lecture provenance: llm.
  await cards.first().getByRole("button", { name: "Éditer la formule" }).click();
  const formulaInput = page.getByLabel("Nouvelle formule");
  // A formula with a scripted reading in the golden corpus, so the inverse
  // reading comes back non-degraded and labeled provenance: llm.
  await formulaInput.fill("EF (eat_1)");
  await cards.first().getByRole("button", { name: "Enregistrer" }).click();
  await expect(page.getByText(/Formule mise à jour/)).toBeVisible({ timeout: 60_000 });
  // La re-lecture inverse est affichée, étiquetée provenance: llm (texte serveur verbatim).
  await expect(page.getByText("Re-lecture inverse")).toBeVisible();
  await expect(page.getByText("(provenance : llm)")).toBeVisible();

  // Convergence (P34): the sample profile has a converging pair, so a card
  // MUST offer the human fusion arbitration - unconditional assertion.
  const fuseButton = page.getByRole("button", { name: "Arbitrer la fusion (accepter)" });
  await expect(fuseButton.first()).toBeVisible({ timeout: 60_000 });
  await fuseButton.first().click();
  await expect(page.getByText(/Fusion arbitrée|Fusion refusée/)).toBeVisible();
  // L'historique des fusions reste affiché (append-only).
  await expect(page.getByText("Fusions arbitrées (humain uniquement)")).toBeVisible();

  // Étape 3 - vérifications MC: runs postés par le harnais sur le vrai moteur.
  await page.locator("nav button[data-step='verifications']").click();
  await expect(page.locator("#run-list li").first()).toBeVisible({ timeout: 120_000 });
  await expect(page.locator(".budget-line")).toContainText("Budget MC");
  // The runs are posted by the harness on the real engine: wait for a real
  // verdict (a slow run is a slow verdict, never a watchdog error - P39).
  // The step reads the runs on mount only, so reload until the verdict lands.
  await expect
    .poll(
      async () => {
        const verdict = await page.locator(".run-verdict").first().textContent().catch(() => null);
        if (verdict === null || verdict === "—") {
          await page.reload();
          await page.locator("nav button[data-step='verifications']").click();
        }
        return verdict;
      },
      { timeout: 240_000, intervals: [2_000, 5_000] },
    )
    .not.toBe("—");
  // Le lecteur de témoin pas à pas (P32) est couvert par les tests composants
  // et les tests moteur ; la maquette ne produit pas de trace témoin sur le
  // moteur réel pour ces questions MC, donc pas d'assertion navigateur ici.

  // Étape 4 - synthèse: contrat, dossier de preuve cliquable, export Markdown.
  await page.locator("nav button[data-step='synthese']").click();
  await expect(page.locator("#contract-preview")).not.toBeEmpty({ timeout: 60_000 });
  const dossier = page.locator("details.proof-dossier").first();
  await expect(dossier).toBeVisible({ timeout: 60_000 });
  await dossier.locator("summary").click();
  await expect(dossier.locator(".proof-list")).toBeVisible();
  // Les pièces du dossier (run, outil, verdict, technique, coût) sont listées.
  await expect(dossier.locator(".proof-list li").first()).toContainText("run ");
  const downloadPromise = page.waitForEvent("download");
  await page.locator("#export-markdown").click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/\.md$/);

  // Bascule de langue fr -> en: le chrome est traduit, pas les données.
  await page.locator("#language-select").selectOption("en");
  await expect(page.locator("nav button[data-step='synthese']")).toHaveText("4. Summary");
  await expect(page.locator("nav button[data-step='questions']")).toHaveText("2. Static questions");
  // Les formules du contrat restent telles quelles (données serveur, jamais traduites).
  await page.locator("nav button[data-step='synthese']").click();
  await expect(page.locator("#contract-preview")).toContainText("AG");
});
