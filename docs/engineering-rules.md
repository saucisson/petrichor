# Agent-era engineering rules

This document records the engineering and protocol rules for
agent-led repositories: the economics that motivate them, the
language-agnostic core, the per-language profiles, the review
checklist additions, and the night loop. It is constitutional
(ADR-0008): every change requires human acceptance. Where it overlaps
`docs/rules.md` or `docs/protocol.md`, this document governs.

---

## Part 0 - The economics (why these rules have this shape)

**P1. The recurring cost is the reference currency.** Code is now read
and rewritten by agents on every session, far more often than it was
ever read by humans. Every rule below is priced in one currency: the
recurring per-session token cost of reading and navigating the code,
against the one-off cost of rare events (dependency migration, big
refactoring, data migration).

**P2. The AI era collapsed the one-off costs and left the recurring
cost untouched.** Writing, migrating, refactoring: an order of
magnitude cheaper. Reading, navigating, holding context: unchanged.
Practices optimized for expensive change (pre-paid abstraction,
speculative genericity) therefore degrade; practices that minimize
every-session reading cost rise.

**P3. Indirection is a recurring tax, and the insurance logic
inverts.** Every layer of redirection costs a context-load round trip
on every future task. Historically, abstraction was pre-paid as
insurance against expensive migration; when migration became cheap,
the premium stopped being worth the payout. Abstract on evidence of
co-evolution or proven volatility, never in anticipation.

**P4. What is durable changes, and rules concentrate force there.**
Code and dependencies: mobile, cheap to rewrite. Data, wire formats,
contracts, secrets, published history: the only irreversible
artifacts of a system. The strongest rules live at those durable
boundaries; everything inside is deliberately fluid.

**P5. Detection points have a strict cost order: compilation < test <
review.** Types and CI are the cheapest, most deterministic oracles,
and an agent cannot negotiate with a compiler. Tests sample behavior
and cannot be argued with if written against contracts. Human review
is the scarcest resource; it must be aimed at what no other oracle
sees.

**P6. Honesty is structural, not moral.** An agent under a passing
constraint will attempt to adjust the oracle rather than the code.
Every rule that makes the oracle immovable (never weaken a test,
fail-closed drivers, human-only merges to `main`, no agent-edited
directives) is not politeness: it is the control system that makes
delegation safe in the first place.

**P7. No pre-paid ceremony.** Architecture layers exist here for two
reasons only: (a) keeping the domain pure and deterministic, so the
cheapest oracles (compiler, property tests) apply to it; and (b)
marking the durable boundaries of P4. They are not dependency-swap
insurance. The correct shape is onion layers without
ports-and-adapters ceremony.

---

## Part I - Language-agnostic core

### A. Working method and protocol

- **A1.** Two roles, declared before work: developer agent
  (implements; merges reviewed PRs into `daily` through the merge
  driver) and reviewer agent (isolated context, seeks
  counterexamples, never merges). No agent ever merges `main`.
- **A2.** Codified merge criteria, not vibes: CI green on the PR head,
  zero BLOCKING findings in the review report, change not on the
  escalation list. The merge driver refuses everything else. No
  platform auto-merge.
- **A3.** One issue at a time, one branch per issue (`feat/<issue>`,
  `fix/<issue>`), PRs under the size budget of R8, one fresh session
  per PR. Session residue rationalizes the next issue; the repository
  records (worklog, PRs, issues) are the continuity, not the context.
- **A4.** The morning directive is human property. Agents never edit
  it.
- **A5.** Budgets: 3 attempts per issue across the whole
  develop-review loop; third failure labels `blocked` +
  `needs-human` and moves on. Answered `needs-human` issues take
  priority over new work. Urgent escalation is a full stop of merges.
- **A6.** Honesty rules: report evidence actually run and say so when
  not; never weaken a test, rule, threshold or check to pass it;
  never guess an acceptance criterion (no criteria = blocked); full
  verification gate (`make check` / `npm run verify`) before
  reporting anything.
- **A7.** The escalation list is the inventory of durable boundaries:
  security and secrets; architecture changes; schema and data
  migrations; constitutional files; ADR-worthy decisions. Everything
  on it requires the human before work starts. Dependencies are not
  on it: N2 owns the dependency loop end to end.
- **A8.** The reviewer session of a consequential change runs a
  different model family than the developer session. This decorrelates
  the systematic errors of generator and judge, which no prompt
  discipline achieves.

### B. Architecture and abstraction

- **B1.** Onion, data-first: a layer imports only from layers
  strictly inside it; the domain contains no I/O; the domain is pure
  computation over data. Enforced mechanically (import-linter,
  structure checks), never by discipline.
- **B2.** No ports-and-adapters ceremony. A capability needed by inner
  code is a named parameter; the composition root picks the
  implementation; the parameter type is the contract. Formal ports are
  reserved for capabilities with multiple live implementations (e.g.,
  a delivery channel with several live transports), never for
  singletons dressed as interfaces.
- **B3.** Abstraction on proof of co-evolution. Duplicate first;
  abstract at the third real occurrence, and ask whether these copies
  evolve together and for the same reason. The reviewer flags
  speculative generality - the generator's native failure mode:
  interfaces with one implementation, factories of one product,
  options nobody passes, premature frameworks.
- **B4.** Blast-radius accounting. Two independent copies that change
  for different reasons beat one shared abstraction coupling two
  modules. DRY is an evidence-based decision, not a reflex.
- **B5.** Composition root only: no global singletons, no service
  locators, adapters constructed nowhere else.
- **B6.** Deterministic by construction: the domain uses no clock, no
  randomness, no environment. Time- and environment-dependent values
  are injected.

### C. Typing

- **C1.** One reference type checker per repo, strict profile, wired
  into pre-commit and CI; its verdict is the truth. Any
  dynamic/unchecked escape carries an inline justification comment.
- **C2.** Distinct types for domain primitives; nominal/branded
  identifiers declared once and produced only at the parse boundary;
  closed sets as enums or literal unions; discriminated unions with an
  explicit discriminator; type guards that narrow, not bare booleans.
- **C3.** Parse, don't validate: external input becomes domain types
  at the boundary; inner code trusts its types and re-checks nothing.
- **C4.** The type checker is the cheapest oracle and cannot be
  negotiated with by an agent. The strictness budget is already paid
  (annotation costs collapsed); the remaining fight is against
  counterfeits: `any` escape hatches, cast corridors, ignored
  diagnostics.
- **C5.** No type gymnastics. Strict and simple beats clever and
  dense. Deep conditional types and type-level metaprogramming
  degrade both the agent's debugging loop and the reviewer's
  counterexample search. If a type expression needs a paragraph to
  explain, simplify it.
- **C6.** Exhaustiveness is the refactoring map. Every closed set
  keeps its guard so that adding a variant makes the compiler list
  every site that must be treated. Where available, encode invariants
  so illegal states do not compile (typed sums over boolean flags,
  distinct types instead of bare strings). This is the deterministic
  complement of the test suite, which only samples.

### D. Errors and observability

- **D1.** Expected errors are typed values (Result patterns, typed
  diagnostics); exceptions mean bugs. Causes are preserved; nothing is
  swallowed. The domain is total.
- **D2.** Logging replaces comments: `DEBUG` narrates sections with
  named fields, the narration is pinned by tests,
  `INFO`/`WARNING`/`ERROR` follow the milestone/recoverable/failure
  contract, rendering is configured only in the composition root, no
  direct stdout outside entry points and `scripts/`, no secret ever
  logged.
- **D3.** Error messages are written for the machine that will read
  them to debug: what was attempted, on what input, which invariant
  broke, where. A good error message shrinks the agent's repair loop;
  a bad one launches a blind search.
- **D4.** Observability is an agent-facing oracle: environments are
  reproducible and ephemeral; failure reports (exceptions, logs)
  carry the structured context the agent needs to act. Debuggability
  of the failure channel is part of the protocol.

### E. Tests

- **E1.** The executable specification is the contract: domain
  changes keep the property tier green; a refactor is not done until
  the properties pass.
- **E2.** Every behavior change ships with an assertion that fails if
  the behavior regresses; narration contract pinned by tests; the
  suite never weakens (A6 binds).
- **E3.** Pyramid: heavy layer = integration and contract tests
  against real boundaries (HTTP, storage, trace formats); middle =
  unit tests on pure domain logic, densified with property-based
  tests; thin = E2E on critical paths only, hunted for flakiness with
  authority (a flaky test is fixed or deleted; an agent believes a
  flaky test). Forbidden: structural tests that mirror the
  implementation.
- **E4.** Mocks at the system edge only (clock, external APIs). Never
  between classes the project owns: prefer inexpensive reals
  (in-memory stores, ephemeral databases, fakes owned by the repo)
  over mock mirrors of the implementation. A mock-heavy unit suite
  fails on every legitimate refactoring, and the agent then adjusts
  the tests - exactly what A6 forbids.
- **E5.** Characterization before big refactors. Before any structural
  sweep, generate integration-level characterization tests against
  current observable behavior; refactor under them; only then retouch
  the suite where contracts intentionally moved.
- **E6.** Mutation testing as a periodic audit of the suite itself,
  not a per-commit gate: a few times a year, before major versions, on
  core modules. Coverage is no longer a signal (an agent games it at
  will); mutation score is the discriminating measurement. Keep a
  coverage floor for CI discipline; stop pretending it measures
  safety.
- **E7.** Snapshots: allowed only at true format boundaries (wire
  payloads, golden traces), small and explicit; forbidden for internal
  structure, where they generate noise per intentional refactor.
- **E8.** Suite speed paces the agent loop: keep the full verification
  gate in minutes and its fast tiers in seconds; isolate slow tiers
  (E2E) from the loop rather than tolerating them; parallelize before
  trimming assertions.

### F. Durable data

- **F1.** Schema and data migrations are on the escalation list (A7).
  This part is where pre-caution remains profitable: the data
  outlives the code (P4 is inverted here - the code is mobile, the
  data accumulates).
- **F2.** Version what survives the process, only that. Production
  data, exported documents, wire/event traces, public contracts:
  explicit versions and upgrade paths. Ephemeral, regenerable data
  (caches, derived indexes, dev databases): a recreate script, not a
  migration framework. No premium on a risk that does not exist.
- **F3.** Migrations: forward-only, idempotent, written before the
  code that needs them, never in the same blind commit as a schema
  change. No down migrations: restore from backup, then fix forward.
- **F4.** Expand/contract by default: add, backfill, switch,
  contract - never break the shape mid-deploy. At agent iteration
  speed the old and new code coexist more often, not less.
- **F5.** Persisted documents and event traces carry an explicit
  schema version; readers of historical data fail loudly on versions
  they cannot handle rather than improvising. Upgrade functions are
  pure and chained `v_n -> v_{n+1}`, property-tested for idempotence
  and for composition. Any persisted format on which deterministic
  replay matters gets a hard version gate: the reader rejects every
  version it does not implement, superseded and unknown alike.
- **F6.** A versioned, anonymized snapshot of real data is a
  first-class test asset: it tests migrations against genuine legacy
  shapes (not fixture-clean tuples) and doubles as the
  characterization corpus of E5. Store it, refresh it, version it.
- **F7.** Decision rule regenerate-vs-migrate: if data can be
  recomputed from a source, prefer recomputation over incremental
  migration - cheaper in tokens and risk, and the upgrade function is
  then a fresh, tested build.

### G. Repository, documentation, machine context

- **G1.** Every directory has one job; long-lived utilities
  self-contained in `tools/`; the product in its language's
  conventional layout; a new top-level directory is ADR-worthy.
- **G2.** Every non-obvious choice has an ADR recording the why (the
  part no agent can infer from the code). Template ADRs number
  0001-0099; an instantiated project numbers its own ADRs from 0100
  upward. The human accepts every ADR.
- **G3.** The worklog is the audit trail, generated by the driver,
  part of the work, never an afterthought.
- **G4.** English everywhere in repository artifacts: identifiers,
  comments, docstrings, log messages, commits, PRs, issues, documents.
  Answer the human in their language; the artifacts are English.
  Enforcement is mechanical: an english-only checker runs as a
  verification front, and a commit-msg hook rejects non-English
  subjects. Review is a late detector and costs review-loop
  iterations; the checker catches it at commit time.
- **G5.** Docs have exactly two valid kinds: durable why (ADRs,
  invariants, rules) and contracts (maps over what the machine cannot
  infer). Written for a machine reader: explicit, stable names, binary
  where possible, no narrative prose that rots. Anything that is
  neither a why nor a contract is deleted, not maintained.
- **G6.** The rules and checklist documents are the reviewer's bundle
  by construction: keep them quotable (one rule, one sentence, one
  testable violation). Assessment prose belongs in an ADR, not in the
  checklist.

### H. Review

- **H1.** The reviewer works in isolated context from a deterministic
  bundle (PR spec, diff, rules, ADRs); it is forbidden from the
  developer's reasoning; it seeks counterexamples, never rates
  quality; it reports binary findings (BLOCKING / TO-FIX /
  SUGGESTION) with `file:line` evidence as a JSON report; the driver
  recomputes counts and fails closed (invalid answer = protocol
  failure, never mergeable).
- **H2.** The reviewer never fixes, never merges, one report per run,
  labels never bend the report, and a BLOCKING has no override inside
  the agent loop (a human overrule merges on the record, outside the
  loop).
- **H3.** Human review is aimed, not exhaustive. Reading order: tests
  and contracts first, then the durable boundaries (auth, schema,
  migrations, public API, secrets, trace formats), then a sampled
  middle. Exhaustive line review of generated code is a dead
  practice; the honest choice is a designed allocation of the
  attention budget, not a rubber stamp disguised as review.
- **H4.** An isolated-model pre-review is the first pass that flags
  systematic generator errors; the human arbitrates what it surfaces;
  approval authority stays human. Two models agreeing on
  plausible-but-wrong remains the known failure mode: the pre-review
  filters, it never certifies.
- **H5.** Review is sequential, per commit: the PR body declares the
  global goal and the commit order as the frame; each commit is read
  against its own message, a declared contract (message matches
  content, one idea, the anchor test of any behavior change in the
  same commit); the walk closes with one composition pass over the
  whole sequence (goal reached, no commit undoes what another set).
  Per-commit greenness (compile plus fast front on every commit) is
  a driver gate in the `make probe-merge` family of N5, not reviewer
  attention; the reviewer reports what the mechanical oracles cannot
  see.

### I. Git and flow

- **I1.** `main` merged by the human only; `daily` receives agent
  work; rebase-only integration; `sync-daily` reconciles `main`
  movement early; conflicts escalate; `main` is never rewritten;
  unattended sessions never sync.
- **I2.** Long-lived branches are wrong. Agents resolving textual
  conflicts well does not buy semantic reviewability of an
  accumulated diff. The day cycle (small PRs, rebase-only, fresh
  sessions) is trunk discipline; feature flags, not feature branches,
  are the activation mechanism.
- **I3.** Commit semantics: an agent's scratch iterations are squashed
  into units of one idea each, the worklog entry attached. Git
  history is a corpus that agents consume to reconstruct intent;
  "fix bug" is a machine-context loss, not a style nit. Conventional
  Commits and generated changelogs stay; the unit boundary is a
  human-owned decision. Merges are rebase-only:
  what lands on the target branch is the branch's own commits,
  nothing is squashed or rewritten at merge time, so squash happens
  in the branch before the PR is merged; one-idea units arrive
  intact on `main`, and review follows the same grain (H5).
- **R8.** PR size budget: ~500 changed lines for all repos. If a
  change genuinely cannot split, the split proposal in the PR body is
  the escape valve, and the reviewer flags the exception. Review
  locality is the one constraint the whole chain depends on (H3).

---

## Part II - Per language and environment

### II.1 Python

Baseline: uv, Ruff, mypy (strict, reference checker), structlog,
pytest, import-linter, pre-commit, release-please.

- Keyword-only parameters everywhere the signature takes more than
  the subject.
- mypy strict is the reference (C1); `Any` and `type: ignore` are
  escapes requiring an inline justification.
- Property tier targeted, not sprinkled: hypothesis properties encode
  invariants (idempotence, monotony, round-trip, boundary emptiness)
  rather than random-input fuzz for its own sake. The generator
  writes the nominal case well and the edges badly; aim the tier
  there.
- Pydantic at boundaries only, as the parse front of C3: external
  input becomes domain records; the interior stays plain
  strict-typed code without re-validation.
- Doubles policy of E4: fake HTTP servers and in-memory stores over
  mock.patch of owned modules; patching is legitimate only at the
  system edge.
- Mutation testing (mutmut or equivalent) on the core modules per E6,
  periodic and budgeted.
- structlog named fields, JSON rendering in production configured in
  the composition root (D2); httpx with typed payloads; no secrets
  in logs.

### II.2 TypeScript, Node, React

Baseline: strict `tsc` over two configurations (workshop without
Node types; server with `@types/node`), type-checked ESLint, a
single `verify` gate, structure checks.

- Two-config strictness: Node strips types at runtime and never checks
  them; the type front lives in the compiler and CI only. The domain
  keeps its purity (no Node types) so the cheapest oracles apply to
  it.
- Branded identifiers declared once and produced only at the parse
  boundary; discriminated unions with `kind`; exhaustive switches
  with `never`; `unknown` narrowing at edges; no `any`; named
  exports only; one concept per file; no barrels; single-object
  named parameters; linear functions with
  `// ---- phase ----` banners; bounded nesting; functional data
  handling with mutation only where the mechanism is mutation and is
  marked.
- Expected errors as values; the domain is total; the executable
  specification (properties) is the contract; deterministic domain
  (no clock, no randomness, no environment access).
- React discipline: components render, hooks think; no business logic
  in JSX; explicit `language` prop threading, no context smuggling.
- Edge-coverage enforcement of C3: every `fetch`, `localStorage`,
  file or HTTP entry goes through a type guard or a branded factory,
  and the structure check (not just the reviewer) verifies it.
- Snapshot discipline of E7: golden traces at the wire boundary
  only; no internal-tree snapshots. Keep the Playwright E2E layer
  thin and authoritative on flakiness (E3).
- Mutation testing (Stryker) as the periodic E6 audit with an
  explicit score threshold and budget, not as a per-commit gate.

### II.3 Rust

Baseline: rustfmt, clippy `-D warnings`, nextest (or cargo test),
gitleaks, the english-only front, all as `make check` fronts; worklog
entry with every commit and PR.

- The compiler is the strongest oracle of the family: exhaustiveness
  is native (enums + match), absence is `Option`, purity by immutable
  data. Use it as the primary enforcement of the C rules.
- Version gates on persisted formats: every replayable artifact (JSONL
  traces, permalinks, exports) carries an explicit version; the
  reader hard-rejects every version it does not implement; responses
  rebuild byte-identical from the trace. Mandatory for any persistent
  or permalinked surface, not a trace-specific habit.
- Cross-check oracles as standing practice: every optimized kernel
  keeps a naive reference kernel, and tests assert agreement on small
  inputs. Generalize to every performance claim a reduction makes.
- serde discipline at boundaries: tagged enums for wire variants,
  explicit version discriminator, `deny_unknown_fields` where replay
  determinism matters (and permissive readers where forward
  compatibility is the goal - F5 decides per format).
- Non-inferable constraints live as doc comments on the type they
  constrain (e.g., send-ness or freshness requirements on a runner
  type): these are durable why (G5), exactly the facts an agent
  cannot infer and will violate in refactorings.

---

## Part III - Review checklist additions

Every item binary, `file:line` evidence on every no:

- [ ] Every new abstraction answers the co-evolution question (B3);
      single-implementation interfaces and unpassed options are
      findings.
- [ ] No mock between classes the project owns (E4); doubles are reals
      or edge mocks with a named field reason.
- [ ] No structural test: the assertion fails on a behavior
      regression, not on a structure change (E2, E3).
- [ ] No snapshot of internal structure (E7).
- [ ] Any schema/data migration: forward-only, idempotent,
      expand/contract compliant, nothing depending on it in the same
      commit (F3, F4).
- [ ] Any persisted/wire format change ships: version bump + upgrade
      function (pure, chained, property-tested) + hard rejection gate
      for unhandled versions (F5).
- [ ] Any new dependency ships its community-validation evidence in
      the PR body, plus a real execution test and the correct
      runtime/dev classification; missing or self-referential
      evidence is a BLOCKING finding (N2).
- [ ] Big refactors (multi-file structural sweeps) are preceded by
      characterization tests in the diff or in an immediately prior
      PR (E5).
- [ ] Suite runtime did not regress past the budget; slow tiers were
      isolated, not assertions trimmed (E8).
- [ ] Error messages carry what/where/which-invariant (D3).
- [ ] Squashed scratch iterations; one idea per commit; worklog entry
      present (I3).
- [ ] Commit walk: each commit's message matches its diff and states
      one idea; any behavior change carries its anchor test in the same
      commit; no commit depends on a later one to be green (H5, I3).
- [ ] No `any` escape, cast corridor, or ignored diagnostic without
      an inline justification (C1, C4).

---

## Part IV - Night loop

Unattended (`make night`) execution is the throughput mode; the
morning is a short, by-usage acceptance session. The rules below make
an interrupted run leave progress rather than debris, and make the
human verdict land in the repository as data.

**N1 - Draft-ADR + flag.** The agent drafts an ADR (>=0100) inside its
PR; the merge into `daily` is allowed with the ADR in `draft` status
and the feature behind a flag, default off. Human acceptance (a
morning action, minutes) flips the ADR to `accepted` and enables the
flag. Constitutional files are still never edited by agents; drafts
add no such risk.

**N2 - Dependency admission by argued evidence, not by list.** No
pre-populated package allowlist: a maintained list silently delegates
the judgment to whoever last edited it, and it goes stale. The agent
that wants a new dependency argues community validation itself, in
the pull request: sustained maintenance, adoption beyond the
package's own documentation, an ecosystem standing it can point to.
The reviewer verifies the evidence, not the taste - an unargued or
self-referential adoption is a BLOCKING finding. A real execution
test and the correct runtime/dev classification ship with the
change; an installation smoke of the packaged artifact still catches
classification misses. The mechanical fronts fail closed in CI on
every pull request: the license gate rejects every license outside
the repository's configured set (configuration, not constitution),
and the vulnerability audit rejects every known CVE in the locked
graph. Dependency bots target the integration branch only, grouped,
with auto-merge for dev minor bumps.

**N3 - Imperfect proposals + verification.** A night run ends with a
morning verification package, not a "done" claim: (a) `daily`
deployed to the staging URL, candidate flags on there only; (b) human
test scenarios for what shipped (see N8); (c) a per-PR report (worklog
link, ADR drafts, review verdict). Doctrine: merge-behind-flag, then
verify-by-usage, for everything outside the durable boundaries. An
interrupted run leaves a claimable partial PR plus its package, which
counts as progress.

**N4 - Mechanical English.** The english-only checker runs as a
`make check` front on every repo, plus a commit-msg hook rejecting
non-English subjects. Prose in comments, identifiers, worklogs and
titles is checked at commit time, not discovered by review.

**N5 - Tooling `daily` -> `main`.** Merge probes run as a local
dry-run (`make probe-merge`), never as PRs. The driver slices
automatically and opens the merge PRs with a generated report
(per-commit provenance, worklog refs, flag inventory). Automating the
merge itself (same criteria as A2, flags off, smoke green on the
deployed artifact, instant rollback) is a constitutional option: it
amends A1 and requires explicit human acceptance.

**N6 - Worklog stamping by the driver only.** PR-number stamping and
entry generation live in `make pr`; agents commit no worklog
corrections. Review still audits the worklog's claims against the
diff.

**N7 - A human queue with a visible service level.** `day-report`
lists the pending human queue (open needs-human, ADR drafts awaiting
acceptance, staging flags awaiting verdict). Capacity rule: if the
queue exceeds its threshold, the night loop switches to maintenance
tasks (docs, benches, allowlist bumps) instead of generating more
pending validation. Nothing stagnates invisibly.

**N8 - The validation console (in-product human verification
loop).**

These console rules are directives only: this template carries the
doctrine; each instantiated repository implements its own console
mechanics per product surface (`docs/deployment.md` records the same
per-repo principle for the deployment channels).

*8.1 Scenario source (driver-enforced).* Every PR with user-visible
impact ships a validation scenario, written by the developer agent,
in `docs/validations/v-<PR>.yaml`:

```yaml
id: v-<PR>
pull_request: <PR number>
issue: <issue number, optional>
flag: <feature flag name, optional - present when gated by N1>
summary: two lines maximum - what changed, why it is visible
steps: ordered observable steps, ending at the interesting part
deep_link: recipe that pre-drives the app to the interesting part
oracles: what CI and the reviewer already attested on this PR
```

Purely internal work (kernel, benches, docs) declares
`observation: none` instead. The merge driver refuses a user-visible
PR without a scenario, and the reviewer verifies `observation: none`
claims on diffs that visibly changed behavior.

*8.2 Served queue.* `GET /api/validations` - owner-only (OIDC),
returns the pending scenarios for the current build together with the
deployed commit SHA. The contract is versioned per F5
(`schema_version`; unknown versions rejected loudly). The manifest
ships with the artifact: no dedicated database on day one.

*8.3 Console UI* (develop mode of each deployed site, owner account
only): a queue ordered by age. Each card shows the summary, the
steps, the already-attested oracles (so the owner's verdict is
acceptance, never re-testing), and a **take me there** action that
executes the deep-link recipe: pre-navigation and pre-fill up to the
interesting part, then the hand returns to the human. Per-site
mechanics: permalink-native apps use their permalinks (the permalink
is the request, replay is deterministic); React apps use a seeded-state
query or a dedicated localStorage entry. Two buttons conclude a card:
validate, or reject with a mandatory comment (optional on validation -
it becomes the body of the follow-up issue).

*8.4 Append-only ledger.* `POST /api/validations/{id}/verdicts`
appends one record to a versioned append-only JSONL ledger (the F5
gate pattern applied to acceptance data):

```json
{"schema_version": 1, "scenario": "v-418", "build_sha": "<deployed commit>",
 "verdict": "validated|refused", "oidc_subject": "<owner>",
 "comment": "<text>", "timestamp": "<ISO 8601>"}
```

The `build_sha` binding prevents stale verdicts on superseded builds.
The day driver commits the ledger back into the repository: human
acceptance becomes an auditable repository artifact, not a
server-side database to back up.

*8.5 Feedback into the flow* (the day driver syncs verdicts): PR
comment + `validated`/`refused` label; a rejection opens a follow-up
issue with the owner's comment as body, placed at the front of the N7
human queue; when the scenario was gated by a draft-ADR flag (N1), a
validation enables the flag in production. `day-report` shows the
queue, the verdict history, and the merge-to-verdict latency - the
night loop's health metric.

*8.6 Stated limits.* Finite scope in v1: queue, drive-to, verdict,
sync - no boards, no metrics dashboards. The console never re-tests
what CI already attests. Endpoints are owner-only, never public (P4
boundary: the ledger is acceptance data). Capacity note: the owner is
the throughput ceiling of a denser loop - if a night produces eight
scenarios, the morning queue is eight validations; that is the
contract, and the ledger is what makes the ceiling measurable.

---

Not adopted as rules (recorded to close the discussion; revisit only
with new evidence): pre-paid hexagonal scaffolding (P3, P7), coverage
targets beyond the floor, upfront mock contracts, spec-abstraction
cleanups, branch-per-feature.
