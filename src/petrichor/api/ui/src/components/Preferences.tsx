import { useState } from "react";
import { useTranslation } from "react-i18next";
import { SUPPORTED_LANGUAGES, type Language } from "../i18n";
import { clearMistralToken, saveMistralToken, storedMistralToken } from "../mistralToken";
import { SUPPORTED_THEMES, type Theme } from "../theme";

/* Preferences panel (web v1 contract §3/§4, issue #208): the Mistral API
   token (password field, pasteable, masked, clearable, localStorage only),
   plus the theme and language selectors. Opened from the account area of the
   header; no secret ever leaves the browser except as the
   `x-mistral-api-token` header on generation-triggering calls. */

interface PreferencesProps {
  theme: Theme;
  onThemeChange: (theme: Theme) => void;
  onLanguageChange: (language: Language) => void;
  onClose: () => void;
}

export default function Preferences({
  theme,
  onThemeChange,
  onLanguageChange,
  onClose,
}: PreferencesProps) {
  const { t, i18n } = useTranslation();
  const [token, setToken] = useState(storedMistralToken());

  const onTokenBlur = () => {
    saveMistralToken(token);
    setToken(storedMistralToken());
  };

  const onClearToken = () => {
    clearMistralToken();
    setToken("");
  };

  return (
    <div className="preferences-backdrop" role="presentation" onClick={onClose}>
      <section
        className="preferences-panel"
        role="dialog"
        aria-modal="true"
        aria-label={t("preferences.title")}
        onClick={(event) => event.stopPropagation()}
      >
        <h2>{t("preferences.title")}</h2>
        <div className="header-group">
          <label htmlFor="mistral-token">{t("preferences.mistralTokenLabel")}</label>
          <input
            id="mistral-token"
            type="password"
            autoComplete="off"
            spellCheck={false}
            value={token}
            placeholder={t("preferences.mistralTokenPlaceholder")}
            onChange={(event) => setToken(event.target.value)}
            onBlur={onTokenBlur}
          />
          <button id="mistral-token-clear" type="button" className="btn" onClick={onClearToken}>
            {t("preferences.clearToken")}
          </button>
        </div>
        <p className="preferences-hint">{t("preferences.mistralTokenHint")}</p>
        <div className="header-group">
          <label htmlFor="preferences-theme">{t("preferences.themeLabel")}</label>
          <select
            id="preferences-theme"
            value={theme}
            onChange={(event) => onThemeChange(event.target.value as Theme)}
          >
            {SUPPORTED_THEMES.map((candidate) => (
              <option key={candidate} value={candidate}>
                {t("preferences.theme." + candidate)}
              </option>
            ))}
          </select>
        </div>
        <div className="header-group">
          <label htmlFor="preferences-language">{t("preferences.languageLabel")}</label>
          <select
            id="preferences-language"
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
        <button id="preferences-close" type="button" className="btn btn-primary" onClick={onClose}>
          {t("preferences.close")}
        </button>
      </section>
    </div>
  );
}
