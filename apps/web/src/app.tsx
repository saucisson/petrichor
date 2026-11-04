import { DayPhaseBadge } from "./components/day-phase-badge";
import { useCurrentTime } from "./hooks/use-current-time";
import { computeDayPhase } from "./lib/day-phase";

/** Composition root of the web app: wires hooks, data and components. */
export function App() {
  const currentTime = useCurrentTime();
  const currentPhase = computeDayPhase(currentTime);
  return (
    <main className="app">
      <h1>Petrichor</h1>
      <p>The autonomous day, at a glance.</p>
      <DayPhaseBadge phase={currentPhase} />
    </main>
  );
}
