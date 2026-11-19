# Fiabilité de la revue — constat des incidents du nightly (vague 8, issues #130–#135)

- **Date** : 2026-11-19
- **Contexte** : le harnais de revue ADR-0010 (`scripts/review.py` + `scripts/session.py`, lancé par `python -m scripts agent-review`) a connu quatre échecs récurrents sur le nightly, consignés ici avec leurs faits mesurables. Ce document est le constat de session qui légitime la vague 8 « Outillage — fiabilité de la revue » (milestone #8, issues P58–P63 = #130–#135).

## Mécanique actuelle (lue dans le commit)

- `scripts/review.py` (ADR-0010) : le bundle de revue = contrat de rôle (`docs/agents/reviewer.md`) + règles d'architecture + checklist + **tous les ADRs in extenso** (`collect_decisions()`) + description de PR + **diff intégral** (`git diff --no-color {base}...HEAD`, aucune exclusion, aucun garde-fou de taille). Codes de sortie : 0 clean, 1 blocking, **2 échec de protocole (pas un verdict)**, **3 préconditions non remplies (la revue n'a pas démarré : pas une tentative)**. Module **stdlib only** (doit tourner dans l'image CI la plus nue), répertoire de travail `.review/`, session lancée par `python -m scripts agent-review` (fichiers `bundle.md`, `system-prompt.md`, `prompt.md`).
- `scripts/session.py` : `MAXIMUM_SESSION_TURNS = 8` **constant codé en dur, non configurable** ; `REVIEWER_TIME_LIMIT_SECONDS` (défaut 600 s) configurable ; `REVIEWER_MODEL` et `REVIEWER_MAX_TOKENS` existants ; `REVIEWER_CLI_COMMAND = "vibe"` codé en dur.

## Incidents constatés

1. **Skew de version du CLI** : premier lancement de revue en exit 2 opaque — le binaire local (vibe 2.26.1) ne connaît pas `--prompt-file` (le driver attend vibe 3.x). Contournement ad hoc : vibe 3.1.3 installé à part + wrapper PATH **temporaire**, non pérennisé.
2. **Revue P40 (PR #120), bundle gonflé par le modèle vendu** (model.pnml, 68,7 Ko) : la session reviewer a atteint sa **limite de tours** (échec runtime, pas un rapport) ; relance manuelle.
3. **Revue P42 (PR #122), bundle 164 Ko** (record d'exécution régénéré + `audit.json` machine-générés) : échec sur **deadline** (600 s) → relance à `REVIEWER_TIME_LIMIT_SECONDS=1800` → échec sur **limite de tours**. Le premier essai sur ce même bundle avait pourtant réussi : comportement **de bord stochastique**, pas du flak.
4. **Boucle de relance aveugle** : le nightly relance la revue à l'identique, sans plafond, sans distinguer la cause (deadline / tours / protocole), sans escalade humaine (`make notify` existe pourtant).

## Diagnostic (acté)

Le reviewer n'est pas mauvais, il est sous-dimensionné : deux budgets (tours, temps) serrés en même temps, un bundle qui grossit des deux côtés (tous les ADRs in extenso + artefacts machine dans le diff), et un harnais qui échoue de manière opaque au lieu de refuser proprement.

## Décisions actées (bornent les issues #130–#135)

1. **Exclusions déterministes** dans le `DIFF UNDER REVIEW` : les artefacts machine n'entrent plus dans le diff, remplacés par une ligne de digest (chemin, taille, sha256, test qui les contrôle). L'isolation de contexte « le bundle est tout le contexte » est **préservée** : c'est le même bundle, débarrassé du bruit que l'audit CI contrôle déjà mieux que le reviewer (ex. `test_the_committed_execution_record_passes_the_audit`).
2. **Taille de bundle = précondition explicite** : un bundle trop gros est refusé avant la session (sémantique du code de sortie « préconditions non remplies » — pas un verdict, pas une tentative), avec un message actionnable.
3. **Budgets configurables** : `MAXIMUM_SESSION_TURNS` devient `REVIEWER_MAX_TOURS` (env, défaut relevé à 12), symétrique de `REVIEWER_TIME_LIMIT_SECONDS`.
4. **Preflight du CLI** : vérification de version/capacité avant lancement, échec rapide avec message explicite ; `REVIEWER_CLI_COMMAND` surchargeable par env (fin des wrappers PATH ad hoc).
5. **Relances bornées** : plafond à 2 tentatives, causes typées dans le message d'échec, escalade humaine via `make notify`.
6. **La sélection des ADRs dans le bundle relève de l'ADR-0010** : vraie révision de doctrine, traitée par un spike dédié (P63) — jamais en drive-by dans une PR de code.

## Issues créées (vague 8, milestone #8 `Outillage — fiabilité de la revue`)

| Issue | Titre | Rôle |
|---|---|---|
| #130 (P58) | Diff de revue assaini : exclusions déterministes et digests | décision 1 |
| #131 (P59) | Garde-fou de taille de bundle en précondition | décision 2 |
| #132 (P60) | Budgets de session configurables et preflight du CLI | décisions 3 + 4 |
| #133 (P61) | Relances bornées, causes typées et escalade humaine | décision 5 |
| #134 (P62) | Mesure : fiabilité de la revue dans le day-report | courbe « taille de bundle vs échecs » |
| #135 (P63) | Spike : réviser ADR-0010, la place des ADRs dans le bundle | décision 6 |
