# ADR-0022 : Worklog par fichiers d'enregistrement (worklog v2)

## Statut

Accepté (draft dans le PR d'implémentation, accepté par l'humain via la
directive de session — partie 3 du brief).

## Contexte

`WORKLOG.md` est un fichier unique de ~106 Ko, appendi en tête par chaque
PR au format appris par lecture du fichier. Trois coûts : lecture
accidentelle du monolithe par l'agent, croissance non bornée, conflits de
rebase ; plus une duplication avec le corps du PR (Changes, Testing
evidence, Judgement calls).

## Décision

Un fichier par enregistrement dans `docs/worklog/`, destination humaine
uniquement, génération automatique par le driver :

- `YYYY-MM-DD-pr-<N>.md` : carte d'index par PR, générée par `make pr`,
  commitée sur la branche du PR (`worklog: PR #N`).
- `YYYY-MM-DD-direct-<slug>.md` : entrée par commit direct sur `daily`,
  créée depuis `docs/worklog/_template.md` dans le même commit.
- `archive-2026-11-12.md` : l'ancien `WORKLOG.md`, archivé inchangé.

Aucun agent ne lit `docs/worklog/`. L'audit du soir passe par
`make day-report`.

## Conséquences

- Suppression du monolithe et des conflits de rebase associés.
- Suppression de toute lecture agent du worklog (économie de contexte).
- Le driver `make pr` génère l'entrée sans intervention de l'agent.
- Les commits directs portent leur entrée template dans le même commit.

## Préséance

Cet ADR prend la préséance sur les clauses worklog des ADR-0009, ADR-0010
et ADR-0017, ainsi que sur toute clause des fichiers constitutionnels
suivants qui ordonne d'écrire ou de lire `WORKLOG.md` :

- `AGENTS.md` (honesty rules, session context, end-of-day audit)
- `docs/agents/developer.md` (reporting section, session continuity, recovery)
- `docs/agents/orchestration.md` (§3, §7, §8)
- `docs/branching.md` (end-of-day step)
- `docs/review-checklist.md` (§7)
- `.vibe/prompts/night-issue.md`, `.vibe/prompts/night-auto.md`

Accord humain : directive de session du 2026-11-12 (partie 3 du brief),
qui vaut accord explicite pour ce changement constitutionnel.
