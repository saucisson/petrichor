# ADR-0014: A `tools/` directory - the template ships tools, not product code

- Status: accepted
- Date: 2026-11-06

## Context

The template must stay applicable to any language and any product: an
instance starts from the protocol and the working tools, and its
product code is written per instance, in the language's conventional
layout. Shipping product scaffolding would force every instance to
start by evicting placeholder code, and every architecture rule would
narrate example code instead of real code. Long-lived utilities, on
the other hand (the `notify` contact tool), genuinely belong in the
repository and must not be conflated with the product.

## Options considered

- **`tools/` for long-lived utilities, product code created per
  instance in the language's standard source layout** (chosen): the
  template ships the protocol, the decisions and the tools; each
  instance's product code lives in its own directory (e.g. `src/`
  in Python) once the instance starts building.
- **Ship a product skeleton in the template**: placeholder code the
  first real issue must evict; also hard-codes a language - rejected.
- **Everything in one source tree**: the doorbell tool and the product
  would share a build, a release cycle and a blast radius - rejected.

## Decision

- `tools/` holds the repository's long-lived utilities; each tool is
  self-contained (its own source, tests, manifest and README). The
  `notify` tool (ADR-0011) is the worked example of the whole
  discipline.
- The template ships **no product code**: an instance's product code
  is created by the coding agent, in the instance's language, in its
  conventional layout, under the same architecture and style rules.
- A new top-level directory is an ADR-worthy decision, not a
  convenience.

## Consequences

- Instances of any language start clean: nothing to evict, nothing to
  translate.
- Utilities evolve and release independently of the product; a change
  to a tool never risks the product's blast radius.
- Negative: the rules are illustrated on tool code (`notify`) rather
  than product code - accepted: the discipline is identical, and the
  first product pull request of an instance applies it for real.
