import { describe, expect, it } from "vitest";
import fr from "../src/locales/fr.json";
import en from "../src/locales/en.json";

/* Harnais de parité i18n FR/EN (contrat web v1 §4, issue #205/#209) : les
   deux catalogues doivent avoir exactement le même arbre de clés - aucune
   chaîne ne manque d'une traduction. */

function keyPaths(value: unknown, prefix: string[] = []): string[] {
  if (value === null || typeof value !== "object") {
    return [prefix.join(".")];
  }
  return Object.entries(value as Record<string, unknown>).flatMap(([key, child]) =>
    keyPaths(child, [...prefix, key]),
  );
}

describe("parité i18n FR/EN", () => {
  it("les catalogues fr et en ont exactement le même arbre de clés", () => {
    const frKeys = keyPaths(fr).sort();
    const enKeys = keyPaths(en).sort();
    expect(frKeys).toEqual(enKeys);
  });

  it("aucune valeur de traduction n'est vide", () => {
    for (const catalog of [fr, en]) {
      for (const path of keyPaths(catalog)) {
        const value = path.split(".").reduce<unknown>((acc, key) => {
          return (acc as Record<string, unknown>)[key];
        }, catalog);
        expect(typeof value).toBe("string");
        expect((value as string).trim().length).toBeGreaterThan(0);
      }
    }
  });
});
