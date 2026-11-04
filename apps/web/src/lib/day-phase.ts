// lib: pure domain logic. No React, no I/O, no imports from other layers
// (ADR-0018) - the equivalent of `models` + `services` in the Python onion.

/** The three phases of the agent working day (docs/branching.md). */
export type DayPhase = "morning" | "work" | "freeze";

const MORNING_ENDS_AT_HOUR = 10;
const WORK_ENDS_AT_HOUR = 18;

/** Human-readable label per phase - the record IS the closed set. */
export const DAY_PHASE_LABELS: Readonly<Record<DayPhase, string>> = {
  morning: "Morning directive",
  work: "Sequential work",
  freeze: "End-of-day freeze",
};

/** Pure: the phase of the agent day at the given timestamp. */
export function computeDayPhase(timestamp: Date): DayPhase {
  if (timestamp.getHours() < MORNING_ENDS_AT_HOUR) {
    return "morning";
  }
  if (timestamp.getHours() < WORK_ENDS_AT_HOUR) {
    return "work";
  }
  return "freeze";
}

/** Pure: the phase that follows the given one, cyclically. */
export function nextPhase(phase: DayPhase): DayPhase {
  switch (phase) {
    case "morning": {
      return "work";
    }
    case "work": {
      return "freeze";
    }
    case "freeze": {
      return "morning";
    }
    default: {
      // Exhaustiveness guard: adding a phase without a case fails the
      // build here - the TypeScript `assert_never` (ADR-0018).
      const exhaustivePhase: never = phase;
      throw new Error(`Unhandled day phase: ${String(exhaustivePhase)}`);
    }
  }
}
