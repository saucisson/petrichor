import { DAY_PHASE_LABELS, computeDayPhase, nextPhase } from "./day-phase";

describe("computeDayPhase", () => {
  it("is morning before the directive ends", () => {
    expect(computeDayPhase(new Date(2026, 10, 3, 9, 30))).toBe("morning");
  });

  it("is work between the directive and the freeze", () => {
    expect(computeDayPhase(new Date(2026, 10, 3, 12, 0))).toBe("work");
  });

  it("is freeze after merges stop", () => {
    expect(computeDayPhase(new Date(2026, 10, 3, 20, 0))).toBe("freeze");
  });
});

describe("nextPhase", () => {
  it("cycles through the whole day", () => {
    expect(nextPhase("morning")).toBe("work");
    expect(nextPhase("work")).toBe("freeze");
    expect(nextPhase("freeze")).toBe("morning");
  });
});

describe("DAY_PHASE_LABELS", () => {
  it("labels every phase - the record IS the closed set", () => {
    const everyPhase = ["morning", "work", "freeze"] as const;
    for (const phase of everyPhase) {
      expect(typeof DAY_PHASE_LABELS[phase]).toBe("string");
    }
  });
});
