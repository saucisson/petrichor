import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import fr from "./locales/fr.json";
import en from "./locales/en.json";

/* i18n of the UI chrome only (spec §9 v1.3, issue #124): French by default,
   English available. The selector choice is persisted in localStorage and
   mirrored on <html lang>. ONLY the chrome is translated - every piece of
   study data produced by the server (formulas, verdicts, witnesses, LLM
   narrations, contract, journal, API error messages) is rendered verbatim
   by the components and must never pass through t(). */

export const LANGUAGE_STORAGE = "petrichor.language";
export const SUPPORTED_LANGUAGES = ["fr", "en"] as const;
export type Language = (typeof SUPPORTED_LANGUAGES)[number];
export const DEFAULT_LANGUAGE: Language = "fr";

export function isLanguage(value: string | null): value is Language {
  return value !== null && (SUPPORTED_LANGUAGES as readonly string[]).includes(value);
}

export function storedLanguage(): Language {
  const stored = localStorage.getItem(LANGUAGE_STORAGE);
  return isLanguage(stored) ? stored : DEFAULT_LANGUAGE;
}

export function applyLanguage(language: Language): void {
  void i18n.changeLanguage(language);
  localStorage.setItem(LANGUAGE_STORAGE, language);
  document.documentElement.lang = language;
}

const initialLanguage = storedLanguage();
document.documentElement.lang = initialLanguage;

void i18n.use(initReactI18next).init({
  resources: {
    fr: { translation: fr },
    en: { translation: en },
  },
  lng: initialLanguage,
  fallbackLng: DEFAULT_LANGUAGE,
  interpolation: { escapeValue: false },
});

export default i18n;
