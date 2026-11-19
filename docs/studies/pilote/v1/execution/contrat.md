# Contrat d'élicitation — session `ses_043aa4`

- Modèle : `mdl_10c2c092`
- Version du contrat : 1.0
- Export rejouable depuis l'état de session.

## Profil arbitré

| Entrée | Éléments | Rôle proposé | Rôle retenu | Statut |
| --- | --- | --- | --- | --- |
| `product_slots` | productSlots_1..2 | ressource partagée | ressource partagée | accepté |
| `option_slots` | optionSlots_1..2 | ressource partagée | ressource partagée | accepté |
| `ready` | ready_1..8 | section critique | section critique | accepté |
| `wait` | wait_1..8 | état d'automate | état d'automate | accepté |

## Hypothèses proposées

| Entrée | Rôle proposé | Confiance | Preuve |
| --- | --- | --- | --- |
| `product_slots` | ressource partagée | 0.90 | les emplacements produit sont des jetons partages entre les recettes |
| `option_slots` | ressource partagée | 0.90 | les emplacements d'options sont des jetons partages |
| `ready` | section critique | 0.85 | ready_i marque la machine prete a delivrer la boisson i |
| `wait` | état d'automate | 0.95 | wait_i est l'etat d'attente de la commande i |

## Questions par famille

### liveness

| ID | Formule | Triage | Risque | Coût | Statut | Vérif. croisée | Preuves |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `q_f420ef4e4a4f` | AG (ready_1 == 1 -> AF ready_1 == 0) | MC | 5 | 2 | accept | vérifiée par 1 outils | [run_a97c29](/v1/runs/run_a97c29) |

### recovery

| ID | Formule | Triage | Risque | Coût | Statut | Vérif. croisée | Preuves |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `q_e853037347cf` | AG EF (productSlots_1 >= 1) | MC | 4 | 1 | accept | vérifiée par 1 outils | [run_5bdada](/v1/runs/run_5bdada) |
| `q_f5a4bec74e93` | AG EF (optionSlots_1 >= 1) | MC | 4 | 1 | accept | vérifiée par 1 outils | [run_292bb2](/v1/runs/run_292bb2) |

### safety

| ID | Formule | Triage | Risque | Coût | Statut | Vérif. croisée | Preuves |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `q_a0839f755b80` | AG (productSlots_1 <= 1) | statique | 5 | 0 | accept | vérifiée par 1 outils | [run_8cfcce](/v1/runs/run_8cfcce) |
| `q_c570f90fa00f` | AG (optionSlots_1 <= 1) | statique | 5 | 0 | accept | vérifiée par 1 outils | [run_91344e](/v1/runs/run_91344e) |
| `q_e9b70c39dd85` | AG (wait_1 <= 1) | statique | 3 | 0 | accept | vérifiée par 1 outils | [run_41ec01](/v1/runs/run_41ec01) |


## Dossiers de preuve

### Clause `q_a0839f755b80` — AG (productSlots_1 <= 1)

- Statut : accept — vérif. croisée : vérifiée par 1 outils
- [run_8cfcce](/v1/runs/run_8cfcce) — 4.4.2 — verdict satisfied — technique verifypn-4.4.2, coût 0.002s

### Clause `q_c570f90fa00f` — AG (optionSlots_1 <= 1)

- Statut : accept — vérif. croisée : vérifiée par 1 outils
- [run_91344e](/v1/runs/run_91344e) — 4.4.2 — verdict satisfied — technique verifypn-4.4.2, coût 0.002s

### Clause `q_f420ef4e4a4f` — AG (ready_1 == 1 -> AF ready_1 == 0)

- Statut : accept — vérif. croisée : vérifiée par 1 outils
- [run_a97c29](/v1/runs/run_a97c29) — 4.4.2 — verdict violated — technique verifypn-4.4.2, coût 0.040s

### Clause `q_e853037347cf` — AG EF (productSlots_1 >= 1)

- Statut : accept — vérif. croisée : vérifiée par 1 outils
- [run_5bdada](/v1/runs/run_5bdada) — 4.4.2 — verdict satisfied — technique verifypn-4.4.2, coût 0.037s

### Clause `q_f5a4bec74e93` — AG EF (optionSlots_1 >= 1)

- Statut : accept — vérif. croisée : vérifiée par 1 outils
- [run_292bb2](/v1/runs/run_292bb2) — 4.4.2 — verdict satisfied — technique verifypn-4.4.2, coût 0.039s

### Clause `q_e9b70c39dd85` — AG (wait_1 <= 1)

- Statut : accept — vérif. croisée : vérifiée par 1 outils
- [run_41ec01](/v1/runs/run_41ec01) — 4.4.2 — verdict satisfied — technique verifypn-4.4.2, coût 0.001s


## Budget MC (session)

- Plafond : 3600.000 s de temps outil
- Consommé : 0.121 s (mesuré, jamais estimé)
- Restant : 3599.879 s — aucun report vers une autre session.

## Journal

| Horodatage | Événement | Détail |
| --- | --- | --- |
| 2026-11-19T13:13:25.061157+00:00 | session_created | model_id=mdl_10c2c092 |
| 2026-11-19T13:13:25.064871+00:00 | arbitration_decided | decision=accept, previous_decision=None, question_id=q_a0839f755b80 |
| 2026-11-19T13:13:25.065512+00:00 | arbitration_decided | decision=accept, previous_decision=None, question_id=q_c570f90fa00f |
| 2026-11-19T13:13:25.065918+00:00 | arbitration_decided | decision=accept, previous_decision=None, question_id=q_f420ef4e4a4f |
| 2026-11-19T13:13:25.066331+00:00 | arbitration_decided | decision=accept, previous_decision=None, question_id=q_e853037347cf |
| 2026-11-19T13:13:25.066719+00:00 | arbitration_decided | decision=accept, previous_decision=None, question_id=q_f5a4bec74e93 |
| 2026-11-19T13:13:25.067101+00:00 | arbitration_decided | decision=accept, previous_decision=None, question_id=q_e9b70c39dd85 |
| 2026-11-19T13:13:25.098430+00:00 | run_added | run_id=run_8cfcce |
| 2026-11-19T13:13:40.652501+00:00 | run_added | run_id=run_91344e |
| 2026-11-19T13:13:55.204607+00:00 | run_added | run_id=run_a97c29 |
| 2026-11-19T13:14:11.380543+00:00 | run_added | run_id=run_5bdada |
| 2026-11-19T13:14:25.943640+00:00 | run_added | run_id=run_292bb2 |
| 2026-11-19T13:14:42.138958+00:00 | run_added | run_id=run_41ec01 |
