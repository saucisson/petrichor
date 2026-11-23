import { useTranslation } from "react-i18next";
import type { Zone } from "./SiteHeader";

/* Public landing page "/" (contract docs/consigne-interface-web-v1.md §1,
   issue #205): presentation of the tool, links to the Découvrir and Atelier
   zones. No authenticated call is emitted from this page. */

interface LandingProps {
  onNavigate: (zone: Zone) => void;
}

export default function Landing({ onNavigate }: LandingProps) {
  const { t } = useTranslation();

  return (
    <main className="mt-4" id="landing">
      <section className="card landing-hero">
        <h2>{t("landing.title")}</h2>
        <p>{t("landing.tagline")}</p>
        <p>{t("landing.description")}</p>
        <div className="landing-actions">
          <button
            type="button"
            className="btn btn-primary"
            data-cta="decouvrir"
            onClick={() => onNavigate("decouvrir")}
          >
            {t("landing.ctaExplore")}
          </button>
          <button
            type="button"
            className="btn"
            data-cta="atelier"
            onClick={() => onNavigate("atelier")}
          >
            {t("landing.ctaWorkshop")}
          </button>
        </div>
        <p className="nav-hint">{t("landing.hintNoAuth")}</p>
      </section>
    </main>
  );
}
