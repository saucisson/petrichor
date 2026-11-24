import { useTranslation } from "react-i18next";
import { loginUrl } from "../authSession";

/* Web v1 error page: the workshop without a session (contract §1/§4, issue
   #209). A non-connected visitor gets an explicit message and a single
   "Se connecter avec auth.linard.fr" button; after the OIDC login the browser
   returns to the workshop zone (returnTo = /ui/atelier). Rendered in the same
   charte as the rest of the site (header, theme, language). */

export default function AtelierGate() {
  const { t } = useTranslation();
  return (
    <section className="error-page" id="atelier-gate" aria-labelledby="atelier-gate-title">
      <h2 id="atelier-gate-title">{t("atelier.gateTitle")}</h2>
      <p>{t("atelier.gateMessage")}</p>
      <button
        id="atelier-login"
        type="button"
        className="btn btn-primary"
        onClick={() => window.location.assign(loginUrl("/ui/atelier"))}
      >
        {t("atelier.gateCta")}
      </button>
    </section>
  );
}
