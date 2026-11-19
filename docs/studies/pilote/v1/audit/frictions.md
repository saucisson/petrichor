# Journal de frictions — pilote v1 (2026-11-19, spec §20.10)

Frictions observées pendant la préparation (P40) et l'exécution (P41) du pilote,
catégorisées (UI, élicitation, moteurs, budget) avec sévérité. Session auditée :
`ses_043aa4` (record `docs/studies/pilote/v1/execution/`, audit `audit.json`).

| # | Catégorie | Sévérité | Friction | État |
|---|-----------|----------|----------|------|
| F1 | moteurs | haute | L'image ITS-Tools ne build pas (tag `v10052022` = dump p2, pas de `pom.xml`) : le pilote a tourné **mono-moteur** (verifypn 4.4.2 seul), donc pas de contre-vérification croisée réelle dans les dossiers de preuve. | ouvert — issue #21 (bloqué image) |
| F2 | élicitation | moyenne | Un template à 3 placeholders (`{place}+{place_b}+{place_c}`) instancié sur une entrée à 2 éléments produisait une formule avec un **id canonique** (`pl_wait_3`) illisible par le moteur (HTTP 500 verifypn). | corrigé en P41 : couple non productif skippé et narré (`question_generation_skipped`) |
| F3 | budget | moyenne | `consumed_seconds` mesure le wall-clock autour de l'appel runner, qui inclut `docker compose up/down` (~14 s/run) — pas le temps outil pur du moteur (~0,04 s/run). Sur 6 runs : 87,26 s consommés dont ~0,13 s de moteur. Le plafond de 3600 s est donc majoritairement consommé par l'orchestration conteneur, pas par la vérification. | ouvert — à clarifier pour §12.4 (coût = temps outil rapporté vs temps mesuré) |
| F4 | outillage | basse | Deux exécutions concurrentes de `study-p` dans le même output-dir ont produit un record incohérent (rapport/contrat d'une session, archive des runs de l'autre) — détecté par l'audit P42. L'archive des runs est append-only, sans verrou inter-processus. | ouvert — à traiter si `study-p` est relancé en parallèle |
| F5 | UI | non observé | Le pilote était scripté (API seule), aucune friction UI constatée dans cette exécution. | n/a |

## Note sur F3 (mesure du coût)

Le rapport archive les deux grandeurs : `budget.consumed_seconds` (wall-clock,
87,26 s) et par run `tool_seconds` (temps outil rapporté par le moteur, ~0,13 s
au total). L'audit vérifie la cohérence entre les deux horloges (tolérance 5 %).
