import { useEffect, useState } from "react";
import type { ApiClient } from "./api";
import type { LlmCallState } from "./types";

const LLM_ACTIVITY_POLL_INTERVAL_MS = 1000;

/* P75 (issue #181): while an LLM-backed request is pending, poll the LLM
   layer's call states and expose the in-progress call (usage, started_at) so
   the step can show the active waiting indicator with the real usage label.
   The mock is instantaneous: no call is ever in progress with it. */
export function useLlmActivity(client: ApiClient, active: boolean): LlmCallState | null {
  const [inProgress, setInProgress] = useState<LlmCallState | null>(null);
  useEffect(() => {
    if (!active) {
      setInProgress(null);
      return;
    }
    let cancelled = false;
    const poll = async () => {
      try {
        const calls = await client.getLlmCalls();
        if (!cancelled) {
          setInProgress(calls.find((call) => call.in_progress) ?? null);
        }
      } catch (error) {
        // The indicator is best-effort: a failed poll never blocks the step,
        // but the cause is preserved in the console, never swallowed.
        console.warn("llm-activity poll failed", error);
      }
    };
    void poll();
    const interval = setInterval(() => void poll(), LLM_ACTIVITY_POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [client, active]);
  return inProgress;
}
