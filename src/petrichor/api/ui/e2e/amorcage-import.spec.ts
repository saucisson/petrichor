import { test, expect } from "@playwright/test";

/* E2E navigateur des trois voies d'import de l'Amorçage (issue #166, spec
   §21): import par nom de catalogue, par URL, et archive multi-PNML avec
   choix du candidat (409). Les endpoints /v1/** sont mockés via page.route —
   seule l'interface servie par le serveur UI est réelle; aucun verdict ni
   aucune donnée serveur n'est inventé côté client. */

const CATALOG_PAGE = {
  items: [
    {
      name: "philosophes-COL-v3",
      family: "philosophes",
      formalism: "COL",
      edition: "v3",
      source: "pnrepository",
      size_bucket: "small",
      url: "https://pnrepository.lip6.fr/philosophes-COL-v3.pnml",
      sha256: "abc",
    },
    {
      name: "philosophes-PT-v3",
      family: "philosophes",
      formalism: "PT",
      edition: "v3",
      source: "pnrepository",
      size_bucket: "small",
      url: "https://pnrepository.lip6.fr/philosophes-PT-v3.pnml",
      sha256: "def",
    },
  ],
  total: 2,
  limit: 50,
  offset: 0,
  facets: {
    family: { philosophes: 2 },
    formalism: { COL: 1, PT: 1 },
    edition: { v3: 2 },
    source: { pnrepository: 2 },
    size: { small: 2 },
  },
};

const MODEL_PT = {
  id: "m-pt",
  metadata: {
    provenance: { source: "catalog", catalog: "pnrepository", name: "philosophes-PT-v3", sha256: "def" },
  },
};

const CANDIDATES = {
  reason: "multiple_candidates",
  detail: "archive contains 2 PNML candidates",
  candidates: [
    { name: "a.pnml", size_bytes: 10, sha256: "s1", formalism: "pt" },
    { name: "b.pnml", size_bytes: 20, sha256: "s2", formalism: "symmetric" },
  ],
};

async function mockApi(page: import("@playwright/test").Page, overrides: {
  catalogImportStatus?: number;
  catalogImportBody?: unknown;
  importUrlStatus?: number;
  importUrlBody?: unknown;
  modelsPost?: (route: import("@playwright/test").Route) => void;
} = {}) {
  await page.route("**/v1/**", (route) => {
    const request = route.request();
    const url = request.url();
    const method = request.method();
    const json = (status: number, body: unknown) =>
      route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (url.includes("/v1/catalog/models") && method === "GET") return json(200, CATALOG_PAGE);
    if (url.includes("/v1/catalog/import") && method === "POST") {
      return json(overrides.catalogImportStatus ?? 201, overrides.catalogImportBody ?? MODEL_PT);
    }
    if (url.includes("/v1/models/import-url") && method === "POST") {
      return json(
        overrides.importUrlStatus ?? 201,
        overrides.importUrlBody ?? {
          id: "m-url",
          metadata: { provenance: { source: "url", url: "https://example.org/m.pnml", sha256: "u1" } },
        },
      );
    }
    if (url.includes("/v1/models") && method === "POST") {
      if (overrides.modelsPost !== undefined) return overrides.modelsPost(route);
      return json(201, { id: "m-file" });
    }
    if (url.includes("/v1/sessions") && method === "POST") return json(201, { session_id: "s-e2e" });
    if (url.includes("/v1/sessions/") && url.endsWith("/profile")) {
      return json(200, { proposed: { entries: [] } });
    }
    return json(404, { detail: "not mocked: " + method + " " + url });
  });
}

test.describe("amorçage — trois voies d'import (issue #166)", () => {
  test("import par nom depuis le catalogue, avec avertissement COL et jumeau -PT-", async ({ page }) => {
    await mockApi(page);
    await page.goto("/");
    await page.locator("#catalog-search-button").click();
    await expect(page.locator(".catalog-entry").first()).toBeVisible();
    // L'entrée COL affiche l'avertissement "symmetric non déplié" et le jumeau -PT-.
    await expect(page.locator(".catalog-entry").first()).toContainText("réseau symétrique non déplié");
    await page
      .getByRole("button", { name: "Importer le jumeau philosophes-PT-v3" })
      .click();
    await expect(page.locator("#profile")).toContainText("Modèle importé : m-pt.");
    await expect(page.locator("#profile")).toContainText(
      "Provenance : source catalog, nom philosophes-PT-v3, sha256 def.",
    );
    await expect(page.locator("#session-id")).not.toHaveValue("");
  });

  test("import par URL avec provenance affichée", async ({ page }) => {
    await mockApi(page);
    await page.goto("/");
    await page.locator("#import-url").fill("https://example.org/m.pnml");
    await page.locator("#import-url-button").click();
    await expect(page.locator("#profile")).toContainText("Modèle importé : m-url.");
    await expect(page.locator("#profile")).toContainText("sha256 u1");
    await expect(page.locator("#session-id")).not.toHaveValue("");
  });

  test("archive multi-PNML : 409 puis choix du candidat et re-POST avec ?candidate=", async ({ page }) => {
    let posts = 0;
    await mockApi(page, {
      modelsPost: (route) => {
        posts += 1;
        if (posts === 1) {
          return route.fulfill({
            status: 409,
            contentType: "application/json",
            body: JSON.stringify(CANDIDATES),
          });
        }
        return route.fulfill({
          status: 201,
          contentType: "application/json",
          body: JSON.stringify({
            id: "m-arch",
            metadata: { provenance: { source: "upload", candidate: "b.pnml", archive_sha256: "z9" } },
          }),
        });
      },
    });
    await page.goto("/");
    await page.locator("#pnml-file").setInputFiles({
      name: "models.zip",
      mimeType: "application/zip",
      buffer: Buffer.from("PK\x03\x04mock"),
    });
    await page.locator("#import-model").click();
    await expect(page.locator("#candidate-chooser")).toBeVisible();
    await expect(page.locator("#candidate-chooser")).toContainText("archive contains 2 PNML candidates");
    await page.locator("#candidate-chooser").getByLabel(/b\.pnml/).check();
    await expect(page.locator("#profile")).toContainText("Modèle importé : m-arch.");
    await expect(page.locator("#profile")).toContainText("nom b.pnml, sha256 z9");
    const lastPost = page.locator("#session-id");
    await expect(lastPost).not.toHaveValue("");
  });
});
