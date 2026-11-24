import { useTranslation } from "react-i18next";

/* Web v1 error page: unknown client route under /ui/ (issue #209). The server
   serves the SPA shell for every extension-less /ui/* path; the app itself
   decides whether the route exists and renders this page in the site charte
   when it does not. */

export default function NotFound() {
  const { t } = useTranslation();
  return (
    <section className="error-page" id="not-found" aria-labelledby="not-found-title">
      <h2 id="not-found-title">{t("errors.notFoundTitle")}</h2>
      <p>{t("errors.notFoundBody")}</p>
    </section>
  );
}
