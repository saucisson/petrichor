# ADR-0016: APIs - REST with best practices

- Status: accepted
- Date: 2026-11-06

## Context

Instances of this template will expose HTTP APIs sooner or later
(product endpoints, tool boundaries, integrations). Left unspecified,
agents improvise: verbs that do not match their semantics, error
payloads in five different shapes, versioning by hope. The protocol
needs one API style with rules concrete enough to review mechanically.

## Options considered

- **REST over HTTP/JSON with conventional best practices** (chosen):
  the style every HTTP client, proxy, cache and agent knows best;
  tooling (gateways, mocks, contract tests) is ubiquitous.
- **GraphQL**: powerful for client-driven queries, but moves
  complexity to the server schema and authorization per field -
  overkill for the template's boundary needs.
- **gRPC / RPC**: excellent for internal service-to-service calls,
  but a poor fit for the human- and platform-facing boundaries this
  template's tools expose.
- **Ad-hoc HTTP endpoints**: no contract, no cacheability, no
  standard client - rejected.

## Decision

- APIs are **REST over HTTP with JSON bodies**.
- **Resource-oriented**: URLs name resources (nouns, plural,
  hierarchical), never actions; actions that do not fit a resource
  get an explicit sub-resource (`.../resets`), not a verb in the URL.
- **Standard semantics**: GET is safe and idempotent, PUT/DELETE
  idempotent, POST creates or triggers; the status code tells the
  truth (2xx success, 4xx caller error, 5xx server error) - no
  "200 with error inside".
- **Pagination, filtering and sorting** on list endpoints from day
  one; unbounded responses are a review finding.
- **Errors are typed data**: a stable JSON error payload (code,
  message, identifier of the failing resource), documented once and
  reused everywhere.
- **Authentication and authorization**: standard schemes only
  (e.g. bearer tokens), never bespoke query-string secrets; a
  request is authenticated before it is parsed deeply.
- **Versioning** is explicit and documented (URL prefix or content
  negotiation), decided once per instance, never per endpoint.
- **The contract is a document**: an OpenAPI (or equivalent)
  specification is generated from or kept with the code, and is part
  of the review.

## Consequences

- Any client - human, agent, or third party - can consume the API
  from its documented contract alone; review can check verb, status
  and payload conventions mechanically.
- Caches, proxies and platform tooling behave correctly by default.
- Negative: REST's uniformity occasionally forces an awkward resource
  modeling around an action; the sub-resource convention is the
  escape hatch, and awkwardness stays visible in the review instead
  of hiding in a custom protocol.
