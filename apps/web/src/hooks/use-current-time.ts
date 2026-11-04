// hooks: React primitives over lib data. May import lib, nothing else
// internal (ADR-0018).
import { useEffect, useState } from "react";

const ONE_SECOND_IN_MILLISECONDS = 1_000;

/** The current time, refreshed every second. */
export function useCurrentTime(): Date {
  const [currentTime, setCurrentTime] = useState<Date>(() => new Date());
  useEffect(() => {
    const intervalIdentifier = window.setInterval(
      () => setCurrentTime(new Date()),
      ONE_SECOND_IN_MILLISECONDS,
    );
    return () => window.clearInterval(intervalIdentifier);
  }, []);
  return currentTime;
}
