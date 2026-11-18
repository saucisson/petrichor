# P39 — Comparaison mock / réel (jalon P2, issue #39)

- **Date** : 2026-11-18
- **Contexte** : le scénario e2e P2 (`tests/test_e2e_p2.py`, `@engine`) tourne sur les moteurs réels verifypn 4.4.2 + dummy via docker compose ; le scénario P1 (`tests/test_e2e_p1.py`) tourne sur le mock + mini-moteur embarqué.

## Où le mock servait (P1), et ce que le réel change (P2)

| Étape | Mock (P1) | Réel (P2) |
|---|---|---|
| Verdicts | `FakeRunner` servait une sortie fixée | verifypn (explicite + réductions) calcule réellement ; le second moteur du cross-check est le dummy (verdict `unknown` par design, aucune prétention sémantique) — voir blocage ITS-Tools ci-dessous |
| Témoins | aucun (witness `null`) | traces XML rejouées en étapes §16, cyclicité/back-edge détectés, stockées une fois référencées n fois |
| Coût | `measured_seconds` wall-clock du faux runner | temps outil réel par run, budget MC par session consommé et consigné |
| Latence | synchrone, instantané | asynchrone 202 + polling, timeouts explicites (jamais de verdict par défaut) |
| Déterminisme | rejouable byte-identique | le contrat reste rejouable (coût = `cost.tool_seconds` rapporté par l'outil, pas le wall-clock) ; les verdicts réels sont stables sur Philosophes-3 |
| Dégradations | triage statique du mini-moteur | idem + dégradation explicite hors fragment, et équité « non vérifiable sous équité » (P35) |

## Bug trouvé grâce au réel

Deux bugs réels, trouvés en faisant tourner le scénario sur le vrai verifypn :

1. **PQL n'a pas d'implication `->`.** La formule MCC `AG (eat_1 == 1 -> AF eat_1 == 0)` est une erreur de parse verifypn ; la forme réécrite `AG (not (eat_1 == 1) or AF eat_1 == 0)` est acceptée. La réécriture vit dans `src/petrichor/pql.py` (`pql_formula`, stdlib-only pour être copiée dans l'image moteur), appliquée par `dialect_query(engine="verifypn")` et par `build_pql_query` du serveur. Flèches traitées de droite à gauche (associativité droite), opérande gauche borné à la parenthèse englobante.
2. **`Dockerfile.its-tools` avait une ligne vide après une continuation `\`**, ce qui collait le `COPY --from=builder` dans la commande `RUN` — l'image ne buildait jamais (aucun test `@engine` ne l'utilisait). Corrigé dans ce PR.

## Blocage ITS-Tools (follow-up)

L'image `petrichor-engine-its-tools:10052022` **ne peut pas builder** : le tag `v10052022` du dépôt ITS-Tools est un dump p2 (pas de `pom.xml` à la racine), donc l'étape `mvn package` échoue. Le scénario e2e P2 cross-vérifie donc sur **verifypn + dummy** : deux moteurs réels distincts via deux services compose, le dummy servant de second avis « aucun verdict » (`unknown`), ce qui rend le cross-status `controversial`/`verified_by_n` visible dans le contrat. Le branchement d'un vrai second moteur sémantique (ITS-Tools réparé, ou un autre outil qualifié §17.2) est un follow-up : il ne change que le `ComposeEngineDriver` câblé dans le test, pas le scénario.

## Budget mesuré (consigné)

Le scénario e2e P2 consigne le budget via l'endpoint `/v1/sessions/{sid}/budget` et l'événement de log `e2e_p2_budget_consigne` : consommé > 0, restant ≥ 0, plafond 1200 s, runs comptés. Le plafond et le timeout de run (300 s) sont relevés dans le test : sur un runner CI froid, le premier run verifypn dépasse les 30 s par défaut — un run lent doit être un verdict lent, jamais une erreur de watchdog. La valeur exacte est dans le log CI du test `@engine`.
