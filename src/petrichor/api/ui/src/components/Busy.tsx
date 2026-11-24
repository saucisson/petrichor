import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

/* Shared active-wait indicator (mission §2.5): an animated CSS spinner, the
   label of the ongoing usage and the elapsed seconds, inside a role="status"
   so screen readers announce it. Navigation is never blocked while it shows;
   only double-submit-prone buttons are disabled by the caller. */
export default function Busy({ label }: { label: string }) {
  const { t } = useTranslation();
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const startedAt = Date.now();
    const interval = setInterval(() => {
      setElapsed(Math.floor((Date.now() - startedAt) / 1000));
    }, 1000);
    return () => clearInterval(interval);
  }, []);
  return (
    <span className="busy" role="status">
      <span className="busy-spinner" aria-hidden="true" />
      {label} — {t("common.elapsed", { seconds: elapsed })}
    </span>
  );
}
