import { useTranslation } from "react-i18next";

/* Verdict pictogram (mission §2.6): "true" -> green check pill, "false" ->
   red cross pill, no verdict yet -> "—". The verdict itself is server data:
   rendered verbatim inside the aria-label, never computed client-side. */
export default function VerdictBadge({ verdict }: { verdict: string | null | undefined }) {
  const { t } = useTranslation();
  if (verdict === null || verdict === undefined || verdict === "") {
    return <span className="verdict v-none">—</span>;
  }
  const kind = verdict === "true" ? "v-true" : verdict === "false" ? "v-false" : "v-other";
  return (
    <span className={"verdict " + kind} role="img" aria-label={t("common.verdictAria", { verdict })}>
      {verdict === "true" ? "✓" : verdict === "false" ? "✗" : verdict}
    </span>
  );
}
