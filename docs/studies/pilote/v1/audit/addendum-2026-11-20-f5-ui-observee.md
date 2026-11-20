# Addendum d'audit — réévaluation de la friction F5 (2026-11-20)

- **Date** : 2026-11-20
- **Culture append-only** : cet addendum est un **nouveau document** ; `frictions.md` et `audit.json` committés le 2026-11-19 ne sont jamais modifiés.
- **Issue** : #128 (P57, vague UI v2 — refonte frontend, spec §9 v1.3, §20.10).

## F5 réévaluée : « UI non observé » → fermée

| Friction | Avant (audit 2026-11-19) | Après cet addendum |
|---|---|---|
| F5 — UI non observé | Le pilote était scripté (API seule) ; aucune friction UI constatée dans cette exécution. | La voie UI est désormais **observée en continu** : un parcours e2e navigateur (Playwright/Chromium) joue les 4 étapes + arbitrage + bascule de langue + édition de formule + dossier de preuve + convergence contre la **vraie API servie sur le vrai compose verifypn**, derrière le marqueur `@ui`, dans un job CI distinct (`ui-e2e`, timeout 60 min, images pré-buildées hors watchdog — leçon P39). Rejouable localement par `make ui-e2e`. |

## Ce que l'e2e couvre (critères de l'issue #128)

- Parcours complet des 4 étapes (amorçage/import PNML, cartes/arbitrages, runs/budget/témoins, synthèse/export/journal) en navigateur réel.
- Arbitrage humain des questions, édition de formule avec re-lecture `provenance: llm`, arbitrage de fusion (convergence P34), dossier de preuve cliquable avec rejeu du témoin (P38/P32).
- Bascule de langue fr→en : le chrome est traduit, les données produites par le serveur restent verbatim.
- Le harnais pytest ne joue que ce que l'UI ne peut pas faire (proposition du profil sémantique — aucun endpoint HTTP, spec §14 — et post des checks MC) ; tout le reste passe par le navigateur.

## Limites constatées (consignées, pas lissées)

- Le parcours e2e dépend du moteur réel verifypn : un run lent est un verdict lent (jamais une erreur de watchdog), le job CI a un timeout adapté.
- La proposition de profil et le post des checks restent hors UI (pas d'endpoint) : la friction « amorçage 100 % UI » n'est pas revendiquée.
