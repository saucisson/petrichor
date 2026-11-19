# Contrat d'élicitation — session `ses_20fd1c`

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
| `q_f420ef4e4a4f` | AG (ready_1 == 1 -> AF ready_1 == 0) | MC | 5 | 2 | accept | vérifiée par 1 outils | [run_17baf4](/v1/runs/run_17baf4) |

### recovery

| ID | Formule | Triage | Risque | Coût | Statut | Vérif. croisée | Preuves |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `q_e853037347cf` | AG EF (productSlots_1 >= 1) | MC | 4 | 1 | accept | vérifiée par 1 outils | [run_1db2fa](/v1/runs/run_1db2fa) |
| `q_f5a4bec74e93` | AG EF (optionSlots_1 >= 1) | MC | 4 | 1 | accept | vérifiée par 1 outils | [run_fff206](/v1/runs/run_fff206) |

### safety

| ID | Formule | Triage | Risque | Coût | Statut | Vérif. croisée | Preuves |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `q_a0839f755b80` | AG (productSlots_1 <= 1) | statique | 5 | 0 | accept | vérifiée par 1 outils | [run_377c6f](/v1/runs/run_377c6f) |
| `q_c570f90fa00f` | AG (optionSlots_1 <= 1) | statique | 5 | 0 | accept | vérifiée par 1 outils | [run_f2293c](/v1/runs/run_f2293c) |
| `q_e9b70c39dd85` | AG (wait_1 <= 1) | statique | 3 | 0 | accept | vérifiée par 1 outils | [run_fe7160](/v1/runs/run_fe7160) |


## Dossiers de preuve

### Clause `q_a0839f755b80` — AG (productSlots_1 <= 1)

- Statut : accept — vérif. croisée : vérifiée par 1 outils
- [run_377c6f](/v1/runs/run_377c6f) — 4.4.2 — verdict satisfied — technique verifypn-4.4.2, coût 0.002s

### Clause `q_c570f90fa00f` — AG (optionSlots_1 <= 1)

- Statut : accept — vérif. croisée : vérifiée par 1 outils
- [run_f2293c](/v1/runs/run_f2293c) — 4.4.2 — verdict satisfied — technique verifypn-4.4.2, coût 0.002s

### Clause `q_f420ef4e4a4f` — AG (ready_1 == 1 -> AF ready_1 == 0)

- Statut : accept — vérif. croisée : vérifiée par 1 outils
- [run_17baf4](/v1/runs/run_17baf4) — 4.4.2 — verdict violated — technique verifypn-4.4.2, coût 0.043s

### Clause `q_e853037347cf` — AG EF (productSlots_1 >= 1)

- Statut : accept — vérif. croisée : vérifiée par 1 outils
- [run_1db2fa](/v1/runs/run_1db2fa) — 4.4.2 — verdict satisfied — technique verifypn-4.4.2, coût 0.037s

### Clause `q_f5a4bec74e93` — AG EF (optionSlots_1 >= 1)

- Statut : accept — vérif. croisée : vérifiée par 1 outils
- [run_fff206](/v1/runs/run_fff206) — 4.4.2 — verdict satisfied — technique verifypn-4.4.2, coût 0.038s

### Clause `q_e9b70c39dd85` — AG (wait_1 <= 1)

- Statut : accept — vérif. croisée : vérifiée par 1 outils
- [run_fe7160](/v1/runs/run_fe7160) — 4.4.2 — verdict satisfied — technique verifypn-4.4.2, coût 0.001s


## Budget MC (session)

- Plafond : 3600.000 s de temps outil
- Consommé : 0.123 s (mesuré, jamais estimé)
- Restant : 3599.877 s — aucun report vers une autre session.

## Journal

| Horodatage | Événement | Détail |
| --- | --- | --- |
| 2026-11-19T08:48:52.798690+00:00 | session_created | model_id=mdl_10c2c092 |
| 2026-11-19T08:48:52.802624+00:00 | arbitration_decided | decision=accept, previous_decision=None, question_id=q_a0839f755b80 |
| 2026-11-19T08:48:52.803205+00:00 | arbitration_decided | decision=accept, previous_decision=None, question_id=q_c570f90fa00f |
| 2026-11-19T08:48:52.803655+00:00 | arbitration_decided | decision=accept, previous_decision=None, question_id=q_f420ef4e4a4f |
| 2026-11-19T08:48:52.804086+00:00 | arbitration_decided | decision=accept, previous_decision=None, question_id=q_e853037347cf |
| 2026-11-19T08:48:52.804501+00:00 | arbitration_decided | decision=accept, previous_decision=None, question_id=q_f5a4bec74e93 |
| 2026-11-19T08:48:52.804877+00:00 | arbitration_decided | decision=accept, previous_decision=None, question_id=q_e9b70c39dd85 |
| 2026-11-19T08:48:52.838768+00:00 | run_added | run_id=run_377c6f |
| 2026-11-19T08:49:08.505769+00:00 | run_added | run_id=run_f2293c |
| 2026-11-19T08:49:23.060925+00:00 | run_added | run_id=run_17baf4 |
| 2026-11-19T08:49:38.733907+00:00 | run_added | run_id=run_1db2fa |
| 2026-11-19T08:49:53.299162+00:00 | run_added | run_id=run_fff206 |
| 2026-11-19T08:50:08.976521+00:00 | run_added | run_id=run_fe7160 |
