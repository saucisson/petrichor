# Petrichor — Vague 1 : issues de la phase 0

Issues prêtes à créer via `gh`, conformes au protocole du §19.2 du document de conception : chaque corps cite ses sections de spec (`docs/spec/`), ses critères d'acceptation sont vérifiables par une commande ou un test, le hors-périmètre et les dépendances sont explicites.

**Création.** Ordre 1 → 9 (les `blocked-by` référencent les titres ci-dessous ; le script de peuplement les traduit en numéros réels après création, ex. via `gh issue list` — idempotence sur les titres, `--dry-run` d'abord). Milestone unique : **« Phase 0 — Socle »**. Chaque corps passe par `gh issue create --title "…" --body-file … --milestone "Phase 0 — Socle" --label …`.

**Labels à créer** (s'ils n'existent pas) : `area:api`, `area:ui`, `area:llm`, `area:engine`, `area:static`, `area:docs`, `area:study`, `kind:feat`, `kind:test`, `kind:chore`, `kind:spike`, `p0`, `p1`.

---

## Issue 1 — Amorçage : environnement vérifié, spec importée, CI verte

**Labels** : `kind:chore`, `area:docs`, `p0` · **blocked-by** : —

```markdown
## Contexte (spec : §19.1, §19.0)

L'environnement est donné : WSL2 Ubuntu, dépôt peuplé du template Python « codage IA »,
`gh` authentifié, Docker + compose installés. Rien ne s'écrit avant que tout soit vert.
Les règles du template (workflow, merge, taille des PR, conventions de codage) font foi
pour tout ce qui est générique (§19.0) ; ce dépôt n'ajoute que la couche métier.

## Tâches

1. Amorçage, sorties collées dans le PR :
   - `gh auth status`, `gh repo view`, `docker compose version`
   - `make setup`, `make check` (+ `make ui-check` si le front est présent)
2. Importer le document de conception dans `docs/spec/` **sans modifier les numéros
   de sections** (citables par les issues — §19.0).
3. Créer le milestone « Phase 0 — Socle », les labels du §19.2, et ce jeu d'issues
   (script idempotent sur les titres, `--dry-run` d'abord).
4. Vérifier que la CI GitHub Actions est verte sur `main` tel quel.

## Critères d'acceptation

- [ ] Les commandes d'amorçage passent sans erreur sur WSL2 (sorties dans le PR).
- [ ] `docs/spec/` contient le document de conception complet, sections numérotées inchangées.
- [ ] CI verte sur `main` sans autre commit que la spec et le script de peuplement.
- [ ] Milestone + labels créés ; le script rejoué ne crée aucun doublon.

## Hors périmètre

Toute fonctionnalité métier ; toute modification du template (lint, CI, Makefile) —
si le template pose problème, issue séparée.
```

---

## Issue 2 — Format canonique `petri+json` v1.0 + hachage canonique

**Labels** : `kind:feat`, `area:api`, `p0` · **blocked-by** : #1

```markdown
## Contexte (spec : §13, §5, §12.6)

Le format canonique `petri+json` v1.0 est le schéma de données non menteur : P/T **et**
Symmetric dès le socle (import Symmetric sans dépliage — décision §12.6 ; dépliage en
phase 2 avec certificats). Média type versionné `petri+json` (§5). Le hachage canonique
exclut ids, layout et annotations (§13) : c'est la clé d'idempotence et de golden files.

## Tâches

1. Paquet + schéma `petri+json` v1.0 : places, transitions, arcs, marquage initial,
   formalisme (P/T | Symmetric), ids stables, layout + annotations optionnels.
2. Hachage canonique : exclut ids, layout, annotations — deux modèles identiques à
   renommage/présentation près ont le même hachage.
3. Validation stricte du schéma ; rejet explicite en `problem+json` (§5, §19.4) ;
   jamais de confiance aveugle à l'entrée.

## Critères d'acceptation

- [ ] Fixtures valides/invalides testées (schéma).
- [ ] Test : renommer tous les ids ne change pas le hachage canonique.
- [ ] Test : changer layout/annotation ne change pas le hachage ; changer le marquage
      initial le change.
- [ ] Round-trip sans perte sur la fixture Philosophes-3 (PNML → canonique → hachage stable).

## Hors périmètre

Endpoints HTTP (issue « POST /models ») ; dépliage des Symmetric (phase 2, §18.3).
```

---

## Issue 3 — `POST /models` : import PNML, modèle permanent, idempotence

**Labels** : `kind:feat`, `area:api`, `p0` · **blocked-by** : #2

```markdown
## Contexte (spec : §4 Gérer, §5, §13, §12.6)

Le modèle importé est permanent et read-only (maquette : le modèle n'est jamais édité —
P4 du fil rouge). L'idempotence se fait par hachage (§5). Le Symmetric est stocké
non déplié (§12.6).

## Tâches

1. `POST /models` acceptant `petri+json` et `pnml+xml` (médias versionnés §5),
   authentification `X-API-Key` (§5).
2. Import PNML : P/T d'abord, puis Symmetric stocké non déplié (champ formalisme visible,
   aucun dépliage silencieux).
3. Rejet explicite des PNML invalides : `problem+json`, aucune trace partielle (§19.4).
4. `GET /models/{id}` : inspection seulement ; le modèle n'est jamais modifiable.

## Critères d'acceptation

- [ ] Import de Philosophes-3 en PNML : 201 ; deux imports du même contenu → même hachage,
      même ressource (idempotence par hachage).
- [ ] PNML invalide → `problem+json` explicite, rien de persisté.
- [ ] Symmetric importé, marqué non déplié.
- [ ] Sans `X-API-Key` : refus (test).

## Hors périmètre

Sessions d'élicitation ; transformations (dépliage/réductions) ; analyse statique.
```

---

## Issue 4 — Socle moteurs : compose, driver `dummy`, sortie normalisée, verrou de verdict

**Labels** : `kind:feat`, `area:engine`, `p0` · **blocked-by** : #1, #2

```markdown
## Contexte (spec : §4, §7, §17, §19.4)

Chaque runner embarque son adaptateur de dialecte ; la couche services ne connaît jamais
la syntaxe d'un outil (§7). Sortie brute → vue fine normalisée : verdict, témoin, coût
(§7). Règles moteurs du §19.4 : un moteur = un service compose, conteneur non privilégié,
pas de réseau sortant superflu, healthcheck, timeout toujours défini. Verrou de verdict :
seul le paquet `engines/` produit des verdicts.

## Tâches

1. `docker-compose.yml` : service `dummy` (aucun outil réel), non privilégié,
   healthcheck, réseau minimal, timeout paramétrable.
2. Contrat de driver unifié (référence pour tous les moteurs réels) : entrée = question
   formelle + modèle canonique ; sortie = vue normalisée : verdict, témoin (format §16),
   coût (temps outil, §12.4), technique(s), version d'outil, exit status.
3. Driver `dummy` : verdict scripté, témoin factice conforme au format §16, coût simulé
   déterministe.
4. Test de garde d'imports : aucun module hors `engines/` n'importe le symbole de verdict.

## Critères d'acceptation

- [ ] `docker compose up` démarre le dummy ; healthcheck vert.
- [ ] Un run dummy retourne le JSON normalisé complet, validé par schéma.
- [ ] Gardien d'imports vert (échec si violation).
- [ ] Un driver qui dépasse le timeout est tué et rapporté (test).

## Hors périmètre

Adaptateurs d'outils réels (issue « Jalon » et phase 2) ; contre-vérification (phase 2, §17.4).
```

---

## Issue 5 — Mini-moteur statique embarqué

**Labels** : `kind:feat`, `area:static`, `p0` · **blocked-by** : #2

```markdown
## Contexte (spec : §18.1, §14, §19.4)

Le mini-moteur est le socle du triage statique (§14) : coût nul, réproductible, témoin
natif. Toute réponse statique est soit une preuve, soit une dégradation marquée —
même grammaire d'états que le triage ; jamais un verdict MC de substitution.
Les résultats du fil rouge (déjà établis sur Philosophes-3 pendant la conception)
servent d'oracle écrit dans les tests.

## Tâches

1. Paquet `engines/minipetri` : invariants P/T (noyau d'incidence), siphons/pièges,
   sous-classes structurelles, bornes, composantes fortement connexes (Tarjan),
   BFS borné pour `AG EF`.
2. Brancher le triage statique : réponses marquées (preuve statique vs dégradation),
   schéma de sortie documenté.
3. Déterminisme : ordre d'exploration stable, graine/horloge injectées (§19.4).

## Critères d'acceptation

- [ ] Sur Philosophes-3 : 14 états, 27 arcs, 1 impasse, 6 invariants, 6 orbites, témoin
      de famine cyclique (back-edge) — valeurs écrites dans le test comme oracle.
- [ ] Le mini-moteur ne produit des verdicts que via `engines/` (gardien d'imports).
- [ ] Réponses statiques typées preuve/dégradation (schéma testé).

## Hors périmètre

Invariants polyhédriques/SMT (SMPT, phase 2+, §18.4) ; CTL/LTL (moteurs réels).
```

---

## Issue 6 — Runs asynchrones : `POST /checks` → 202, polling, idempotence

**Labels** : `kind:feat`, `area:api`, `p0` · **blocked-by** : #3, #4

```markdown
## Contexte (spec : §5, §12.1, §17.3, §14)

Checks par famille (§12.1) : reachability, safety, liveness, recovery, deadlock ;
pattern = schéma de corps typé ; pattern hors famille → 422, jamais réinterprété.
Réponse 202 + `Location` vers le run (§5) ; idempotence par hachage. En phase 0,
l'exécution passe par le driver dummy et/ou le mini-moteur ; si la famille exige MC
et qu'aucun moteur réel n'est qualifié → dégradation explicite (§14).

## Tâches

1. `POST /checks/{famille}` avec patterns typés par famille ; `422` + `problem+json`
   si le pattern n'appartient pas à la famille.
2. `202` + `Location` ; `GET /runs/{id}` pour le polling ; états du run documentés.
3. Idempotence par hachage : (modèle, formule, paramètres) identiques → même run.
4. `X-API-Key` requise sur les deux routes.

## Critères d'acceptation

- [ ] `POST /checks/deadlock` sur Philosophes-3 (`EF DEADLOCK`) : 202 + Location ;
      le run atteint un état terminal ; verdict lisible.
- [ ] Rejouer la même requête retourne le même run (id identique) — test.
- [ ] Pattern hors famille → 422 en `problem+json`, jamais exécuté.
- [ ] Sans `X-API-Key` : refus (test).

## Hors périmètre

Contre-vérification croisée et appariement (phase 2, §17.4) ; budget (issue « Archivage
& budget »).
```

---

## Issue 7 — Archivage intégral des runs + budget de session

**Labels** : `kind:feat`, `area:api`, `p0` · **blocked-by** : #6

```markdown
## Contexte (spec : §12.4, §12.5, §7, §20.2)

Coût = temps outil consommé, plafond par session, pas de report (§12.4). Archivage
intégral : le couple (modèle, run) est la donnée d'expérience de l'étude C — le budget
limite ce qu'on produit, jamais ce qu'on garde (§12.5). Purger des runs est un interdit
métier (§19.3).

## Tâches

1. Chaque run archive : commande, version d'outil, timeout, hachage d'entrée, sortie
   brute **et** vue normalisée (§7 — reproductibilité publication, §20.9).
2. Budget de session : plafond configurable (défaut 3600 s, unité §12.4) ; chaque
   commande moteur débite le temps consommé ; dépassement → refus explicite avec
   état du budget ; pas de report.
3. Refus « budget épuisé » : `problem+json` + budget restant visible.

## Critères d'acceptation

- [ ] Un run terminé expose coût consommé ; l'archive contient sortie brute + normalisée
      + hachages (test d'intégrité).
- [ ] Gardien : aucune API de suppression de runs n'existe ; aucun chemin de purge
      dans le code (test structurel).
- [ ] Session à plafond réduit (fixture) : requête au-delà refusée avec budget affiché ;
      pas de report (requête suivante refusée aussi tant que le plafond est atteint).
- [ ] Le coût facturé = temps outil mesuré (le dummy facture son coût simulé).

## Hors-périmètre

Réinitialisation du budget (opération de configuration, pas une API applicative).
```

---

## Issue 8 — Mock LLM déterministe + golden files (corpus Philosophes-3)

**Labels** : `kind:feat`, `area:llm`, `p0` · **blocked-by** : #2, #3

```markdown
## Contexte (spec : §8, §15, §16, §19.4, §20.9)

Le mock déterministe est un citoyen de première classe : exigence de reproductibilité
pour la publication (§20.9). Golden files : `golden/{model_hash}/{usage}.json` (§15),
corpus initial = contenu scripté de la maquette. Sorties typées par usage ; échec de
validation → re-demande bornée puis dégradation explicite, jamais de valeur inventée
(§8). Narrations étiquetées `provenance: llm`, jamais un verdict (§16).

## Tâches

1. `DeterministicMockProvider` (§8) : sert les golden files tels quels ; fournisseur
   scripté sélectionnable par configuration.
2. Corpus initial (maquette) : profil sémantique Philosophes-3 **avec son rôle
   volontairement faux** (gaucher), lectures des 7 formules du catalogue, clusters A/B,
   narrations de témoins.
3. Validation de schéma par usage typé (§15) ; dégradation explicite en cas d'échec.

## Critères d'acceptation

- [ ] Deux exécutions d'une même session scriptée produisent un journal LLM identique
      (test de non-régression).
- [ ] Golden files servis tels quels, chemin indexé par hachage du modèle (test).
- [ ] Une golden file corrompue (fixture) déclenche la dégradation explicite — aucune
      valeur par défaut silencieuse.
- [ ] Gardien : les golden files ne sont jamais réécrits par le code.

## Hors périmètre

Fournisseur LLM réel (Mistral/local, phase 1+) ; prompt engineering ; qualité du LLM
(études B, §20.5).
```

---

## Issue 9 — Jalon phase 0 : bout en bout sur un moteur réel qualifié

**Labels** : `kind:feat`, `area:engine`, `area:api`, `p0` · **blocked-by** : #3, #4, #5, #6, #7, #8

```markdown
## Contexte (spec : §11 phase 0, §17.2, §17.5, §19.4)

Jalon de la feuille de route : importer Philosophes-3, exécuter `EF DEADLOCK` via un
moteur réel qualifié, lire le run. Premier adaptateur réel : **verifypn (TAPAAL)** —
référence explicite et producteur de témoins (§17.2) ; image construite localement
(licence GPL : pas de redistribution), version épinglée + empreinte (§19.4).
Qualification (§17.5) : tests de verdicts connus dans le **vrai conteneur** (oracles du
benchmark MCC), marqués `@engine` — un échec moteur = un échec CI, jamais un skip
silencieux ; un adaptateur non qualifié ne porte jamais seul une clause.

## Tâches

1. Adaptateur verifypn en conteneur (compose), dialecte + sortie normalisée via le
   contrat de driver établi avec le dummy.
2. Qualification `@engine` : verdicts attendus du benchmark reproduits sur les familles
   couvertes (deadlock, reachability).
3. Scénario du jalon, rejoué deux fois (moteur réel puis mock) :
   import Philosophes-3 → `POST /checks/deadlock` → polling → lecture du run :
   verdict, témoin rejouable (pas-à-pas), coût consommé, archive complète.

## Critères d'acceptation

- [ ] Le jalon complet passe sur verifypn réel (test `@engine` bout-en-bout) — sorties
      collées dans le PR.
- [ ] Le même scénario passe sur le mock, journal identique d'un run à l'autre
      (déterminisme).
- [ ] `@engine` : verifypn qualifié (verdicts attendus du benchmark reproduits) —
      sinon l'adaptateur est marqué non qualifié et ne porte pas seul de clause.
- [ ] Coût facturé au budget ; archive du run complète (sortie brute + normalisée).

## Hors périmètre

Second outil de contre-vérification (ITS-Tools, phase 2 — appariement §17.4) ;
élicitation complète (phase 1).
```

---

## Note de traçabilité

- Les références `#N` des `blocked-by` désignent les titres ci-dessus dans l'ordre de création 1 → 9 ; le script de peuplement traduit les titres en numéros réels après création (idempotence sur les titres).
- Toute évolution de ces textes (seuils, périmètre) passe par une PR de modification du présent document **avant** la création des issues — jamais par une retouche silencieuse du script.