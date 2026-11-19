# Pré-enregistrement — étude pilote (P3), version v1

- **Étude** : pilote (P3, spec §20) — la chaîne complète sur un modèle réel, à quel coût.
- **Version d'étude** : v1 — figée le **2026-11-18**, avant la première session (spec §20.2, §20.10).
- **Règle de versionnement** : toute modification d'un matériau figé = nouvelle version d'étude (v2, …), jamais rétro-appliquée. Les matériaux sont épinglés par hash dans `config.json` et dans la trace du corpus golden (`src/petrichor/llm/golden/preregistration.json`).

## 1. Modèle réel choisi (hors corpus Philosophes)

**DrinkVendingMachine-PT-02** — instance P/T dépliée n°02 du benchmark MCC (Model-Checking Contest, mcc.lip6.fr), modèle de L. Hillah.

- Fichier : `model.pnml` (`sha256:aec46498f37608c62ff52fa933c8d17b5f583e305d45c4e1ae0717e064b624d4`)
- Source : `evangelista/mcc-models` (GitLab LIPN), commit `903f7d939b5f6eda299d11d37b661fefac77c214`, récupéré le 2026-11-18
- Import Petrichor : `formalism=pt`, 24 places, 72 transitions, 440 arcs, hash canonique `sha256:10c2c09236fe45d21e203336943e5f9be4d8d2ad648836c6918bbfc3e868946c`
- Espace d'états borné : la session complète est rejouable sous le plafond (critère §20.2) — sonde réelle ci-dessous, ~0,05 s outil par propriété.

## 2. Séance de familiarisation consignée (2026-11-18)

Import du modèle via l'API (`POST /v1/models`), inspection de la structure (places `wait_1..8`, `ready_1..8`, `productSlots_1..2`, `optionSlots_1..2`, `theProducts_1..2`, `theOptions_1..2`), puis **sonde sur le vrai moteur verifypn 4.4.2** (conteneur compose, `ComposeEngineDriver`) :

| Formule | Famille | Verdict | Technique | Temps outil | États explorés |
|---|---|---|---|---|---|
| `EF DEADLOCK` | deadlock | violated (pas d'impasse atteignable) | verifypn-4.4.2 | 0,003 s | 1 |
| `AG (productSlots_1 + productSlots_2 <= 2)` | safety | satisfied | verifypn-4.4.2 | 0,004 s | 0 |
| `EF (ready_1 >= 1)` | reachability | satisfied | verifypn-4.4.2 | 0,003 s | 2 |
| `AG EF (wait_1 >= 1)` | liveness | satisfied | verifypn-4.4.2 | 0,046 s | 0 |

Runs archivés (`/tmp` de la session, archive `RunArchive`) — le modèle est petit, borné, et tourne en quelques millisecondes de temps outil : le plafond de 3600 s laisse une marge énorme.

## 3. Grille de couverture et catalogue de formules figés (v1)

- `catalogue-formules.v1.json` (`sha256:aaafca14…`) : copie byte-identique du catalogue produit `src/petrichor/elicitation_catalog.v1.json` — 11 templates rôles × mots-guides.
- `grille-couverture.v1.json` (`sha256:08700934…`) : les 7 groupes de couverture (§14.6) extraits du catalogue : réflexion, détention, repas, ressource, acquisition gauche, acquisition droite, libération.
- Test de non-dérive : les copies figées doivent rester égales (hash) au catalogue produit ; toute évolution du catalogue passe par une version d'étude v2.

## 4. Seuils go/no-go pré-enregistrés (spec §20.1, §20.10)

| Seuil | Valeur figée |
|---|---|
| Contrat complet exportable | oui (contrat JSON + Markdown avec dossiers de preuve) |
| Coût cumulé | ≤ plafond de session (3600 s de temps outil) |
| Audit « zéro verdict inventé » | journal de session croisé avec l'archive des runs — aucune valeur de verdict sans run correspondant |
| Journal de frictions | catégorisé (UI, élicitation, moteurs, budget) avec sévérités |
| Décision | go (phase 4) ou itération (phase 2), écrite ; si go, au plus k changements majeurs planifiés en issues |

## 5. Plan d'analyse pré-enregistré (spec §20.7)

- Descriptifs + IC 95 % : Wilson pour les proportions, bootstrap pour les coûts.
- Comparaisons appariées : McNemar / binomial exact (sain vs fautif, A vs baseline).
- Correction de Holm si plus d'un test par question de recherche.
- Tailles d'effet rapportées ; aucun ajustement a posteriori des seuils.

## 6. Plafond de session fixé (spec §20.2)

**3600 s de temps outil cumulé** (`session_budget_seconds: 3600.0` dans `config.json`), identique pour toutes les conditions, mesuré via `/v1/sessions/{sid}/budget` (§12.4), jamais débordé.
