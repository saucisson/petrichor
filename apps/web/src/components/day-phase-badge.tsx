// components: presentation. Props are typed interfaces; no business
// logic beyond display decisions (ADR-0018).
import { DAY_PHASE_LABELS, type DayPhase } from "../lib/day-phase";

export interface DayPhaseBadgeProperties {
  phase: DayPhase;
}

const DAY_PHASE_CLASS_NAMES: Readonly<Record<DayPhase, string>> = {
  morning: "badge badge--morning",
  work: "badge badge--work",
  freeze: "badge badge--freeze",
};

/** Present a day phase - nothing more. */
export function DayPhaseBadge({ phase }: DayPhaseBadgeProperties) {
  return (
    <span className={DAY_PHASE_CLASS_NAMES[phase]}>{DAY_PHASE_LABELS[phase]}</span>
  );
}
