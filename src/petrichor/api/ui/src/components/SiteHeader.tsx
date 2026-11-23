import { useTranslation } from "react-i18next";
import { SUPPORTED_LANGUAGES, type Language } from "../i18n";
import type { Theme } from "../theme";

/* Common saucisson web v1 header (contract docs/consigne-interface-web-v1.md
   §4): logo + site name, the three zones (Accueil / Découvrir / Atelier),
   language selector, persistent light/dark theme toggle. The account menu
   ("Se connecter" / préférences / déconnexion) lands with the OIDC login
   (issue #207) and the harmonisation pass (issue #209). */

export const ZONE_IDS = ["accueil", "decouvrir", "atelier"] as const;
export type Zone = (typeof ZONE_IDS)[number];

interface SiteHeaderProps {
  zone: Zone;
  theme: Theme;
  onNavigate: (zone: Zone) => void;
  onToggleTheme: () => void;
  onLanguageChange: (language: Language) => void;
}

export default function SiteHeader({
  zone,
  theme,
  onNavigate,
  onToggleTheme,
  onLanguageChange,
}: SiteHeaderProps) {
  const { t, i18n } = useTranslation();
  // The light logo variant is for dark surfaces, the dark one for light
  // surfaces: the header band is dark, the landing page is light.
  const logoSource =
    theme === "dark" ? "/logos/petrichor-logo-clair.svg" : "/logos/petrichor-logo-sombre.svg";

  return (
    <header>
      <h1>
        <img src={logoSource} alt="" width={22} height={22} aria-hidden="true" />
        {t("app.title")}
      </h1>
      <nav aria-label={t("zones.aria")}>
        <ol id="zones">
          {ZONE_IDS.map((zoneId) => (
            <li key={zoneId}>
              <button
                type="button"
                className="btn"
                data-zone={zoneId}
                aria-current={zone === zoneId ? "page" : undefined}
                onClick={() => onNavigate(zoneId)}
              >
                {t("zones." + zoneId)}
              </button>
            </li>
          ))}
        </ol>
      </nav>
      <div className="header-group">
        <button
          id="theme-toggle"
          type="button"
          className="btn"
          aria-label={t("header.themeToggle")}
          aria-pressed={theme === "dark"}
          onClick={onToggleTheme}
        >
          {theme === "dark" ? (
            <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="12" cy="12" r="4" />
              <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
            </svg>
          ) : (
            <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z" />
            </svg>
          )}
        </button>
      </div>
      <div className="header-group">
        <label htmlFor="language-select">{t("app.languageLabel")}</label>
        <select
          id="language-select"
          value={i18n.language}
          onChange={(event) => onLanguageChange(event.target.value as Language)}
        >
          {SUPPORTED_LANGUAGES.map((language) => (
            <option key={language} value={language}>
              {language === "fr" ? "Français" : "English"}
            </option>
          ))}
        </select>
      </div>
    </header>
  );
}
