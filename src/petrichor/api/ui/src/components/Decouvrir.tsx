import { useTranslation } from "react-i18next";

/* Public "Découvrir" zone (contract docs/consigne-interface-web-v1.md §1,
   issue #206 fills it with the read-only catalogs and models). Read-only by
   contract: no write, no authenticated call. */

export default function Decouvrir() {
  const { t } = useTranslation();

  return (
    <main className="mt-4" id="decouvrir">
      <section className="card">
        <h2>{t("decouvrir.title")}</h2>
        <p>{t("decouvrir.placeholder")}</p>
      </section>
    </main>
  );
}
