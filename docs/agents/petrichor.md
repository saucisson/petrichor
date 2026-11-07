# Petrichor — guide de l'agent de codage

> **Fichier d'entrée de l'agent.** Déposé dans `docs/agents/petrichor.md`, référencé depuis `AGENTS.md` à la racine. Les règles du template s'appliquent telles quelles ; ce fichier n'ajoute que la couche métier.

## Mission

Petrichor assiste l'élicitation et la vérification formelle de réseaux de Petri (P/T, Symmetric) : **l'IA propose des questions, l'ingénieur arbitre, les outils tranchent.** Prototype de recherche avec publication visée.

**Principe inviolable** : aucun verdict n'est jamais produit, écrit ou extrapolé hors des moteurs de vérification (conteneurs) ou du mock déterministe. Si tu hésites sur une valeur de verdict : elle vient d'un run, ou elle n'existe pas.

## Où trouver quoi

| Fichier | Rôle |
|---|---|
| `docs/spec/petrichor.md` | La spécification de référence — 20 sections numérotées, **stables** (les issues les citent). Toute exigence se référence par numéro, ex. « Spec §13 ». |
| `docs/backlog.md` | Source des issues GitHub, créées par `scripts/create_issues.sh` (idempotent, `--dry-run` d'abord) : labels → milestones → issues. |
| `docs/ui-mockup/` | La maquette React d'élicitation (Philosophes-3) : **référence des exigences frontend** (fonctions P1–P12), pas du code de production. |

## Hiérarchie des règles

1. Le principe « l'IA propose, les outils disposent » — inviolable.
2. Les décisions actées : spec §12 et §18.4 — une contradiction est un bug, même si le code marche.
3. Le reste de la spec (`docs/spec/petrichor.md`).
4. Le template du dépôt (workflow, branches, taille des PR, merge, codage, CI) — appliqué tel quel, jamais recopié ni contredit.
5. Tes goûts — en dernier.

Arbitrage : point **générique** d'ingénierie → le template fait foi ; point **métier** (verdicts, budget, spec) → la spec fait foi. Divergence constatée entre code et spec : issue de documentation d'abord, code ensuite. **On n'édite jamais les numéros de sections de la spec**, on n'ajoute que des sections.

## Amorçage (avant toute issue)

```
gh auth status && gh repo view && docker compose version
make setup && make check
```

Tout vert **avant** d'écrire la première ligne. L'environnement est donné, jamais réinstallé : WSL2 Ubuntu, client `gh` authentifié, Docker + compose.

## Boucle de travail (une issue = un PR)

La mécanique — branches, commits, revue, merge, CI — est celle du template, **sans exception ni adaptation**. Ne s'ajoutent que les gardiens métier :

- **Rituel d'entrée** : s'auto-assigner l'issue (`gh issue edit N --add-assignee @me`) et vérifier que ses `blocked-by` sont fermés avant de commencer.
- **Traçabilité** : chaque PR référence `Closes #N`, cite les sections de spec implémentées, et porte les **preuves** (sorties de commandes, résultats de tests, captures si UI).
- **Revue humaine préalable au merge** pour : golden files, fusion/convergence (§12.2), arbitrages, tout ce qui touche à un verdict ou à un budget.
- **Interdits métier** : « fixer » un test de verdict pour le faire passer ; modifier des golden files hors issue dédiée ; purger des runs ou des logs (§12.5 — ils sont la donnée d'expérience des études).

## Gardiens métier (résumé de la spec §19.4 — détail dans la spec)

- **Verrous de verdict** : seul le paquet `engines/` (adaptateurs + mini-moteur) peut produire une valeur de verdict ; un test de garde vérifie qu'aucun autre module ne l'importe.
- **Moteurs** : un moteur = un service `docker compose` ; image construite **localement** (licences GPL : pas de redistribution), version épinglée + empreinte, healthcheck, conteneur non privilégié ; tests `@engine` sur les verdicts attendus du benchmark MCC — un échec moteur = un échec CI, jamais un skip silencieux.
- **Budget & asynchrone** : toute commande moteur passe par le plafond de session, timeout toujours défini ; `202 + Location`, idempotence par hachage et `X-API-Key` testées.
- **Déterminisme du mock** : graine configurable, horloge injectée ; deux exécutions d'une même session produisent un journal identique (test).
- **Entrées PNML** : validées à l'import — rejet explicite plutôt que transformation fausse.

## Définition du terminé (chaque PR)

La définition générique (CI, revue, merge, changelog) est celle du template. En supplément : critères d'acceptation de l'issue cochés et **démontrés dans le PR** ; tests `@engine` verts sur les moteurs concernés ; golden files intacts sauf issue dédiée ; aucune purge de runs, aucune régression du budget.

## Mini-index de la spec

| § | Contenu |
|---|---|
| 1–2 | Objet, principes, vue d'ensemble |
| 3 | Socle technique |
| 4 | Les huit services |
| 5–6 | Contrat d'API transverse, ressources |
| 7 | Adaptateurs outils & dialectes |
| 8–9 | Couche LLM, frontend |
| 10 | Flux de bout en bout (fil rouge) |
| 11 | Feuille de route (phases 0–4) |
| 12 | Décisions actées |
| 13 | Format canonique `petri+json` |
| 14 | Moteur d'élicitation (triage, catalogue, convergence) |
| 15–16 | Contrat LLM, format des témoins |
| 17 | Outils MCC, association aux endpoints, contre-vérification |
| 18 | Analyse statique, réductions certifiées, dépliage |
| 19 | Protocole d'implémentation assistée (ce guide en détail) |
| 20 | Protocole expérimental (études pilote, A–D) |

L'index ne remplace jamais la spec : avant d'implémenter, lire la section citée par l'issue.