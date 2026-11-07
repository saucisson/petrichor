# ADR-0012: Anti-complacency tests - mutation testing and property tests

- Status: accepted
- Date: 2026-11-02

## Context

Goodhart's law applies brutally to AI agents: any metric that becomes
the target (line coverage) will be gamed - tests that execute code
without asserting anything, assertions that can never fail, fixtures
that dodge the hard path. Coverage measures execution, not
verification.

## Options considered

- **Mutation testing on the daily branch + property-based tests for
  domain logic** (chosen): mutants that survive are assertions that
  were never really there; properties are invariants a human forgot
  to check.
- **Raise the coverage floor to 100 %**: intensifies exactly the
  gaming it claims to prevent.
- **Reviewer diligence alone**: reviewers miss non-asserting tests;
  the failure mode is invisible in the diff.

## Decision

- **Mutation testing** runs on `daily` at end of day; surviving
  mutants become issues for the next day.
- **Property-based tests** (the suite's `property` tier) for domain
  invariants - the notification payload contract is the worked
  example.
- Review checklist item: a test must **fail if the behavior
  regresses**, not merely run it.
- For the Python tools in this template: mutmut and Hypothesis.

## Consequences

- "Covered but unverified" code is surfaced daily, mechanically.
- Property tests produce counterexamples humans never imagined - the
  same counterexample-seeking posture as the reviewer agent
  (ADR-0010).
- Negative: mutation runs are slow; that is why they run at end of
  day on the day's boundary, not in every pull request. A heavy
  mutant day is a signal about test honesty, and its cost is
  accepted.
