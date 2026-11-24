import { useTranslation } from "react-i18next";
import type { ApiClient } from "../api";
import { useLlmActivity } from "../useLlmActivity";
import Busy from "./Busy";

/* P75 (issue #181): active waiting during a real LLM call. While the step's
   request is pending, the label comes from the LLM layer's in-progress call
   state (translated usage, e.g. "Lecture de la grille…"); otherwise the
   caller's fallback label is shown. Never a frozen screen without information. */
export default function LlmBusy({
  client,
  active,
  fallbackLabel,
}: {
  client: ApiClient;
  active: boolean;
  fallbackLabel: string;
}) {
  const { t } = useTranslation();
  const inProgress = useLlmActivity(client, active);
  const label = inProgress ? t(`llm.${inProgress.usage}`) : fallbackLabel;
  return <Busy label={label} />;
}
