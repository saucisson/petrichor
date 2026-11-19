# Décision go/no-go — pilote v1 (2026-11-19, spec §20.10, issue #42)

## Seuils pré-enregistrés (P40, `src/petrichor/study/pilote_v1/preregistre.md` §4)

| Seuil | Valeur figée | Mesuré (session `ses_043aa4`) | Résultat |
|---|---|---|---|
| Contrat complet exportable | oui (JSON + Markdown, dossiers de preuve) | 6 questions arbitrées, 6 dossiers de preuve, `contrat.json`/`contrat.md` hachés | OK |
| Coût cumulé ≤ plafond | ≤ 3600 s de temps mesuré | 87,26 s consommés, `within_cap=true` (`rapport.json`) | OK |
| Audit « zéro verdict inventé » | journal × archive des runs | 6/6 contrôles PASS (`audit.json`) : chaque preuve du contrat est adossée à un run archivé (verdict + version + coût), aucun run orphelin, journal cohérent | OK |
| Journal de frictions | catégorisé avec sévérités | `frictions.md` (5 frictions, 1 haute ouverte) | OK |

## Décision : **GO** pour la phase 4 (P4 — études & publication)

La chaîne tient sur un modèle réel hors corpus Philosophes (DrinkVendingMachine-PT-02,
benchmark MCC) : contrat complet, coût très sous le plafond, audit propre.

**Changements majeurs planifiés (k = 2, en issues existantes) :**

1. **#21** — débloquer l'image ITS-Tools pour restaurer la contre-vérification croisée réelle (friction F1, haute).
2. **#20** — qualification complète de l'adaptateur verifypn (CTL/LTL/reachability + témoins sur oracles MCC).

Les frictions F3 (sémantique du coût mesuré vs temps outil) et F4 (verrou inter-processus
de l'archive) sont des points de clarification à planifier par l'humain, pas des
bloquants du go.

## Preuves

- Record d'exécution : `docs/studies/pilote/v1/execution/` (rapport, contrat, journal, runs archivés, tous hachés)
- Audit : `docs/studies/pilote/v1/audit/audit.json` (6/6 PASS, rejouable via `make study-p-audit`)
- Matériaux figés v1 : `src/petrichor/study/pilote_v1/` (hash-épinglés, trace de pré-enregistrement dans le corpus golden)
