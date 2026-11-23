import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { ApiClient } from "../api";
import type { CatalogEntry } from "../types";

/* Public "Découvrir" zone (contract docs/consigne-interface-web-v1.md §1/§5,
   issue #206): read-only view of the MCC model catalog through the public
   route GET /v1/catalog/models - no session, no API key, no write. The client
   is built with an empty key so no credential header is ever sent. */

const PUBLIC_CLIENT = new ApiClient();

export default function Decouvrir() {
  const { t } = useTranslation();
  const [search, setSearch] = useState("");
  const [entries, setEntries] = useState<CatalogEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (term: string) => {
    setLoading(true);
    setError(null);
    try {
      const params: Record<string, string> = { limit: "50" };
      if (term.trim() !== "") params.search = term.trim();
      const result = await PUBLIC_CLIENT.searchCatalog(params);
      if (result.status !== 200 || result.page === undefined) {
        setError(result.detail ?? `HTTP ${result.status}`);
        setEntries([]);
        setTotal(0);
      } else {
        setEntries(result.page.items ?? []);
        setTotal(result.page.total ?? 0);
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load("");
  }, [load]);

  return (
    <main className="mt-4" id="decouvrir">
      <section className="card">
        <h2>{t("decouvrir.title")}</h2>
        <p>{t("decouvrir.intro")}</p>
        <form
          className="decouvrir-search"
          onSubmit={(event) => {
            event.preventDefault();
            void load(search);
          }}
        >
          <label htmlFor="decouvrir-search">{t("decouvrir.searchLabel")}</label>
          <input
            id="decouvrir-search"
            type="search"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
          <button type="submit" className="btn btn-primary">
            {t("decouvrir.searchButton")}
          </button>
        </form>
        {loading ? <p className="nav-hint">{t("decouvrir.loading")}</p> : null}
        {error !== null ? (
          <p className="message message-error" role="alert">
            {error}
          </p>
        ) : null}
        {!loading && error === null ? (
          <>
            <p className="nav-hint">{t("decouvrir.total", { count: total })}</p>
            {entries.length === 0 ? <p className="nav-hint">{t("decouvrir.empty")}</p> : null}
            <ul id="decouvrir-list">
              {entries.map((entry) => (
                <li key={entry.name} className="decouvrir-entry">
                  <strong>{entry.name}</strong>{" "}
                  <span className="badge badge-family">{entry.family}</span>{" "}
                  <span className="badge">{entry.formalism ?? "—"}</span>{" "}
                  <span className="badge">{entry.edition ?? "—"}</span>{" "}
                  <span className="badge">{entry.source}</span>{" "}
                  <span className="badge">{entry.size_bucket ?? "—"}</span>
                </li>
              ))}
            </ul>
          </>
        ) : null}
      </section>
    </main>
  );
}
