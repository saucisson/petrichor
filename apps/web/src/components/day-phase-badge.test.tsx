import { render, screen } from "@testing-library/react";

import { DAY_PHASE_LABELS } from "../lib/day-phase";

import { DayPhaseBadge } from "./day-phase-badge";

describe("DayPhaseBadge", () => {
  it("renders the label of the given phase", () => {
    render(<DayPhaseBadge phase="work" />);
    expect(screen.getByText(DAY_PHASE_LABELS.work)).toBeInTheDocument();
  });
});
