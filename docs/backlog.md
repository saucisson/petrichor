# Petrichor — Backlog complet (vagues 1–6)

Corps de toutes les issues, prêts à peupler GitHub via le script idempotent (§19.2 : labels → milestones → issues, `--dry-run` d'abord). Conventions : **1 issue = 1 PR** d'une session ; chaque corps cite ses sections de spécification (le document « Petrichor — Conception complète », v1.2) ; critères d'acceptation vérifiables par commande ou test ; hors-périmètre explicite ; dépendances notées `blocked-by`.

**Milestones** : `P0 — socle` · `P1 — MVP élicitation` · `Outillage — moteurs & transformations` · `P2 — chaîne MC` · `P3 — pilote` · `P4 — études & publication`.

**Labels** : `area:api` `area:ui` `area:llm` `area:engine` `area:static` `area:docs` `area:study` · `kind:feat` `kind:test` `kind:chore` `kind:spike`.

**Index**

| ID | Titre | Dépend de |
|---|---|---|
| P01 | Amorçage du dépôt : environnement vérifié, backlog peuplé | — |
| P02 | Schéma canonique `petri+json` v1.0 et hachage canonique | — |
| P03 | `POST /models` et import PNML (P/T, puis Symmetric non déplié) | P01, P02 |
| P04 | Compose moteurs, driver `dummy` et contrat de sortie normalisée | P01 |
| P05 | Mini-moteur statique embarqué | P02 |
| P06 | Runs asynchrones : 202 + Location, idempotence, `X-API-Key` | P03, P04 |
| P07 | Archivage intégral des runs et budget de session | P06 |
| P08 | Mock LLM déterministe et golden files (corpus Philosophes-3) | P02 |
| P09 | Adaptateur verifypn — socle minimal (reachability, deadlock, témoin) | P04 |
| P10 | Jalon P0 : bout en bout réel sur Philosophes-3 | P03, P06, P08, P09 |
| P11 | Service Gérer : sessions persistantes et reprise | P03 |
| P12 | Profil sémantique et groupes de couverture | P03, P08 |
| P13 | Amorçage in situ sur le graphe et modèle permanent | P05, P12 |
| P14 | Questions statiques : triage et non-vacuité, dégradation explicite | P05, P12 |
| P15 | Catalogue d'élicitation : 11 templates rôles × guidewords | P14 |
| P16 | Cartes de questions et arbitrages humains (révisabilité, journal) | P11, P15 |
| P17 | Contrat de questions et export Markdown | P16 |
| P18 | Frontend : les 4 étapes de la maquette branchées sur l'API | P11–P17 |
| P19 | Jalon P1 : session d'élicitation complète sans exploration d'états | P11–P18 |
| P20 | Adaptateur verifypn complet (CTL, LTL, reachability, témoins) | P09 |
| P21 | Adaptateur ITS-Tools (toutes familles, LTL stutter) | P04 |
| P22 | Qualification des adaptateurs par oracles du benchmark MCC | P20, P21 |
| P23 | Appariement de contre-vérification par famille | P22 |
| P24 | Réductions certifiées : Token Elimination et rejeu de témoin | P20 |
| P25 | Preuve statique d'abord : ITS-Tools en mode reducer | P21 |
| P26 | Adaptateur SMPT : invariants polyhédriques et reachability SMT | P22 |
| P27 | Dépliage Symmetric → P/T via `mcc` de Dal Zilio, avec oracle | P03, P04 |
| P28 | Spike : TY, le nouveau venu du MCC 2026 | — |
| P29 | Spike : LoLA 2 et l'équité weak/strong | — |
| P30 | Checks par famille : les 5 endpoints et leurs patterns | P23 |
| P31 | Contre-vérification croisée affichée et clauses controversées | P23, P30 |
| P32 | Témoins rejouables et lecteur pas-à-pas | P20, P30 |
| P33 | Budget MC cumulé et plafond par session | P07, P30 |
| P34 | Convergence asymétrique : détection, fusion, propagation | P16, P30 |
| P35 | Équité : `fairness` des runs et clause liée `repairs_run_id` | P29, P30 |
| P36 | Propositions humaines : LLM réel, 4 usages typés | P08, P19 |
| P37 | Formule éditable et re-lecture inverse | P16, P30 |
| P38 | Synthèse complète : dossiers de preuve cliquables | P31, P32 |
| P39 | Jalon P2 : la maquette branchée sur de vrais verdicts | P31–P38 |
| P40 | Préparation du pilote : modèle réel, grille figée, pré-enregistrement | P39 |
| P41 | Exécution du pilote et mesure du coût cumulé | P40 |
| P42 | Audit du pilote, frictions, décision go/no-go | P41 |
| P43 | study-d : rejeu du benchmark MCC et robustesse de la chaîne | P22, P23, P24 |
| P44 | Générateur de fautes d'asymétrisation (f1–f4) avec certificats | P02, P05 |
| P45 | study-c : pipeline d'injection de fautes et oracle par concordance | P23, P44 |
| P46 | Replays LLM et bornage de la variance | P36, P45 |
| P47 | study-b : qualité des propositions LLM (experts) | P36 |
| P48 | study-a : élicitation guidée vs manuelle (sujets humains) | P45, P47 |
| P49 | Analyse, figures et paquet artifact evaluation | P45–P48 |
| P50 | Squelette de publication : papier outil + résultats empiriques | P49 |

---

## Vague 1 — Milestone `P0 — socle`

### P01 · Amorçage du dépôt : environnement vérifié, backlog peuplé

**Labels** `kind:chore` `area:api` · **Dép.** —

**Contexte.** Spec §19.1 (amorçage) et §19.2 (backlog). L'environnement est donné : WSL2 Ubuntu, template Python « codage IA », `gh` authentifié, docker compose.

**Critères d'acceptation.**
- [ ] `gh auth status`, `gh repo view`, `docker compose version` passent et sont consignés dans le PR.
- [ ] `make setup` et `make check` verts avant toute autre modification.
- [ ] Script de peuplement idempotent (`--dry-run` puis création) : labels, milestones et les issues P01–P50 présentes sur GitHub, une seule fois.
- [ ] Aucune règle du template modifiée (au-delà des chemins strictement nécessaires).

**Hors périmètre.** Toute installation d'outil système ; toute réécriture du template.

### P02 · Schéma canonique `petri+json` v1.0 et hachage canonique

**Labels** `kind:feat` `area:api` `area:static` · **Dép.** —

**Contexte.** Spec §13 : ids stables, layout/annotations séparés, hachage canonique **excluant** ids, layout et annotations.

**Critères d'acceptation.**
- [ ] Sérialisation canonique déterministe (clés triées, ids stables) ; round-trip sans perte.
- [ ] Test : renommer les ids, changer le layout ou les annotations **ne change pas** le hachage ; changer la structure ou le marquage initial le change.
- [ ] Validation stricte du schéma ; entrée invalide rejetée avec erreur explicite.

**Hors périmètre.** Import PNML (P03) ; Symmetric (dépend du même schéma, stockage non menteur).

### P03 · `POST /models` et import PNML (P/T, puis Symmetric non déplié)

**Labels** `kind:feat` `area:api` · **Dép.** P01, P02

**Contexte.** Spec §5 (médias types versionnés, `X-API-Key`), §13, §12.6 (Symmetric accepté dès le socle, non déplié).

**Critères d'acceptation.**
- [ ] `POST /models` en `application/pnml+xml` → canonique P/T ; hachage calculé et renvoyé.
- [ ] Symmetric importé et stocké **non déplié** (le schéma ne ment pas sur le formalisme).
- [ ] PNML invalide ou non supporté → rejet explicite (jamais de dépliage faux, cf. §18.5).
- [ ] Sans clé `X-API-Key` → 401 ; lecture du modèle via GET renvoie le canonique.

**Hors périmètre.** Dépliage (P27) ; transformations (vague 3).

### P04 · Compose moteurs, driver `dummy` et contrat de sortie normalisée

**Labels** `kind:feat` `area:engine` · **Dép.** P01

**Contexte.** Spec §4, §5, §17.5, §19.4 : un moteur = un service compose, image construite localement, version épinglée + empreinte, healthcheck, non privilégié, timeout défini.

**Critères d'acceptation.**
- [ ] `docker-compose.yml` avec un service `dummy` conforme aux règles §19.4 (healthcheck, empreinte, timeout configurable).
- [ ] Contrat de driver : question formelle en entrée → JSON `{verdict, witness, coût, technique}` en sortie.
- [ ] Un run dummy produit verdict + witness + coût, archivé au format run (§12.5).
- [ ] Test `@engine` : le driver répond dans le vrai conteneur, échec = échec CI.

**Hors périmètre.** Tout adaptateur réel (P09, P20+).

### P05 · Mini-moteur statique embarqué

**Labels** `kind:feat` `area:static` `area:engine` · **Dép.** P02

**Contexte.** Spec §18.1 (socle du triage), §14. Le mini-moteur est le seul producteur de verdicts statiques ; il doit retrouver les résultats du fil rouge.

**Critères d'acceptation.**
- [ ] P/T-invariants, siphons/pièges, bornes structurelles, Tarjan (SCC), BFS `AG EF`, détection d'impasse.
- [ ] Test de référence : Philosophes-3 → 14 états, 27 arcs, 1 impasse, 6 invariants, 6 orbites, témoin de famine cyclique.
- [ ] Test de garde : seul le paquet `engines/` peut produire une valeur de verdict (§19.4).
- [ ] Rejet explicite des nets hors capacité (pas de résultat silencieux).

**Hors périmètre.** Tout appel à un outil externe.

### P06 · Runs asynchrones : 202 + Location, idempotence, `X-API-Key`

**Labels** `kind:feat` `area:api` · **Dép.** P03, P04

**Contexte.** Spec §5 (polling 202 + Location, idempotence par hachage, clé), §12.1 (garde anti-polymorphisme).

**Critères d'acceptation.**
- [ ] `POST /checks/{famille}` renvoie 202 + `Location` ; `GET` du run suit les états (running, done, error).
- [ ] Même (modèle, question) hachés → même run retourné (test d'idempotence).
- [ ] Pattern `kind` hors famille → 422, jamais réinterprété (§12.1).
- [ ] Sans clé → 401 ; erreur moteur et timeout → états explicites, jamais de verdict par défaut.

**Hors périmètre.** Budget (P07) ; vrais moteurs (P09+).

### P07 · Archivage intégral des runs et budget de session

**Labels** `kind:feat` `area:api` · **Dép.** P06

**Contexte.** Spec §12.4–5 : coût = temps outil consommé, plafond par session, pas de report ; archivage intégral — le budget limite ce qu'on produit, jamais ce qu'on garde.

**Critères d'acceptation.**
- [ ] Chaque run archive : commande, version d'outil, timeout, hachage d'entrée, coût mesuré.
- [ ] Aucune API ni code path ne supprime un run (test).
- [ ] Plafond de session configurable ; dépassement → refus explicite avec reste de budget.
- [ ] Le coût est mesuré (temps outil), pas estimé.

**Hors périmètre.** Affichage du budget dans l'UI (P33).

### P08 · Mock LLM déterministe et golden files (corpus Philosophes-3)

**Labels** `kind:feat` `area:llm` · **Dép.** P02

**Contexte.** Spec §15–16 : sorties typées, golden files `golden/{model_hash}/{usage}.json`, reproductibilité exigée pour l'étude C.

**Critères d'acceptation.**
- [ ] `LLMProvider` scripté : sert les golden files du corpus Philosophes-3 (profil avec rôle volontairement faux, lectures des formules du catalogue, clusters A/B).
- [ ] Chaque sortie validée par son schéma JSON ; échec → re-demande bornée puis dégradation explicite (§15).
- [ ] Test : deux exécutions d'une session produisent un journal **identique** (§19.4).
- [ ] Aucune golden file ne contient de verdict (test de garde sur le corpus).

**Hors périmètre.** LLM réel (P36).

### P09 · Adaptateur verifypn — socle minimal (reachability, deadlock, témoin)

**Labels** `kind:feat` `area:engine` · **Dép.** P04

**Contexte.** Spec §17.2 (Tapaal = référence explicite, producteur de témoins), §17.3 (mapping familles), §19.4 (images locales, épinglées). Ce socle minimal débloque le jalon P0 ; le complet est P20.

**Critères d'acceptation.**
- [ ] Image verifypn construite localement, version épinglée + empreinte, healthcheck.
- [ ] `ReachabilityCardinality` (EF) et `GlobalProperties.ReachabilityDeadlock` exécutables via le driver, verdicts normalisés.
- [ ] Témoin (trace) traduit au format §16.
- [ ] Test `@engine` sur verdicts connus (ex. PhilosopheDeadlock du benchmark) : échec = échec CI.

**Hors périmètre.** CTL, LTL, réductions (P20, P24).

### P10 · Jalon P0 : bout en bout réel sur Philosophes-3

**Labels** `kind:test` `area:api` · **Dép.** P03, P06, P08, P09

**Contexte.** Spec §11 (jalon phase 0) : importer Philosophes-3, exécuter `EF DEADLOCK` via un moteur réel qualifié et lire le run.

**Critères d'acceptation.**
- [ ] Scénario scripté : import → `POST /checks/deadlock` (`EF DEADLOCK`) → polling → verdict + témoin + coût lus depuis l'archive.
- [ ] Le même scénario rejoué sur le mock produit le même journal.
- [ ] Preuves (sorties de commandes) dans le PR ; scénario ajouté à la CI comme test de fumée.

**Hors périmètre.** Contre-vérification (P23) ; UI (P18).

---

## Vague 2 — Milestone `P1 — MVP élicitation`

### P11 · Service Gérer : sessions persistantes et reprise

**Labels** `kind:feat` `area:api` · **Dép.** P03

**Contexte.** Spec §4 (Gérer), maquette P11 (session reprenable) : une session reprend là où elle s'est arrêtée, journal compris.

**Critères d'acceptation.**
- [ ] Création, liste, reprise de session ; état complet sérialisé côté serveur.
- [ ] Reprise après redémarrage du processus (test).
- [ ] Journal horodaté append-only persistant, exportable.

**Hors périmètre.** Multi-utilisateur, droits fins.

### P12 · Profil sémantique et groupes de couverture

**Labels** `kind:feat` `area:api` `area:llm` · **Dép.** P03, P08

**Contexte.** Spec §14 : le profil sémantique (rôles) est **proposé** par le LLM et arbitré par l'humain ; les groupes de couverture en dérivent (un sous-rôle par groupe) ; les groupes non couverts re-sement la grille.

**Critères d'acceptation.**
- [ ] Lecture du profil (mock golden files pour Philosophes-3, rôle volontairement faux inclus) ; endpoints d'arbitrage du profil.
- [ ] Groupes de couverture calculés ; couverture d'une question arbitrée par mention de groupe.
- [ ] Les groupes non couverts sont exposés comme cibles de re-semmage.
- [ ] Aucun rôle n'est appliqué sans arbitrage humain.

**Hors périmètre.** Génération de questions (P15).

### P13 · Amorçage in situ sur le graphe et modèle permanent

**Labels** `kind:feat` `area:api` `area:ui` · **Dép.** P05, P12

**Contexte.** Spec §14 (amorçage), maquette P4 (modèle permanent + amorçage sur le graphe), P5 (survol carte → surbrillance), P6 (couverture grisée sur le net).

**Critères d'acceptation.**
- [ ] Le graphe d'états du mini-moteur est consultable en permanence pendant toute la session.
- [ ] Annotations in situ des éléments (place, transition, arc) persistées dans la section annotations du canonique (hors hachage).
- [ ] API de résolution carte → éléments (pour la surbrillance) ; couverture grisée du net consommable par le front.

**Hors périmètre.** Graphe sur nets non bornés (dégradation explicite si explosion : affichage tronqué marqué).

### P14 · Questions statiques : triage et non-vacuité, dégradation explicite

**Labels** `kind:feat` `area:api` `area:static` · **Dép.** P05, P12

**Contexte.** Spec §14 (triage statique/MC, non-vacuité) ; maquette P8 (chips de non-vacuité).

**Critères d'acceptation.**
- [ ] Chaque question porte un triage `statique | MC | dégradé` avec justification, calculée par la famille et la capacité courante.
- [ ] Questions statiques résolues par le mini-moteur sans run MC ; résultat marqué `technique: static`.
- [ ] Chips de non-vacuité : pour `EF φ`, le marquage initial ne satisfait pas déjà φ ; pour `AG ¬φ`, il ne le viole pas (test sur le corpus).
- [ ] Aucune dégradation silencieuse : l'état de la question l'affiche.

**Hors périmètre.** Runs MC (vague 4).

### P15 · Catalogue d'élicitation : 11 templates rôles × guidewords

**Labels** `kind:feat` `area:api` · **Dép.** P14

**Contexte.** Spec §14 : 11 templates, patterns `famille.kind`, triage par famille, ordre de génération = trous de couverture d'abord, puis risque décroissant × coût croissant.

**Critères d'acceptation.**
- [ ] Les 11 templates implémentés et instanciables sur Philosophes-3 ; chaque question a un identifiant stable et sa formule.
- [ ] `kind` hors famille → 422 (§12.1).
- [ ] Grille de couverture **figée en config versionnée** (prérequis du pré-enregistrement §20.2).
- [ ] Test : la génération cible d'abord les groupes non couverts.

**Hors périmètre.** Arbitrage (P16) ; LLM réel (P36).

### P16 · Cartes de questions et arbitrages humains (révisabilité, journal)

**Labels** `kind:feat` `area:api` · **Dép.** P11, P15

**Contexte.** Spec §4 (Arbitrer), §12.7 ; maquette P1 (révisabilité des arbitrages + journal horodaté).

**Critères d'acceptation.**
- [ ] Endpoints : accepter / refuser / réviser une carte ; révision = nouvelle décision, l'ancienne reste au journal.
- [ ] Journal horodaté append-only : chaque décision avec avant/après.
- [ ] Aucune fusion automatique de questions (la convergence arrive en P34, par Arbitrer seul).

**Hors périmètre.** Convergence, propagation inter-questions (P34).

### P17 · Contrat de questions et export Markdown

**Labels** `kind:feat` `area:api` `area:docs` · **Dép.** P16

**Contexte.** Spec §4, maquette P3 : l'export Markdown agrège le contrat ; ici (MVP) les dossiers de preuve pointent vers des résultats statiques/mock.

**Critères d'acceptation.**
- [ ] Export Markdown : profil arbitré, hypothèses, questions par famille avec statuts, journal.
- [ ] Chaque question arbitrée référence ses preuves disponibles (run statique, run mock) par lien.
- [ ] L'export est rejouable depuis l'état de session (test).

**Hors périmètre.** Dossiers de preuve sur vrais runs (P38).

### P18 · Frontend : les 4 étapes de la maquette branchées sur l'API

**Labels** `kind:feat` `area:ui` · **Dép.** P11–P17

**Contexte.** Spec §9 ; maquette (4 étapes : Amorçage, Questions statiques, Vérifications MC, Synthèse) : toutes ses fonctions sont des exigences frontend (P1–P12).

**Critères d'acceptation.**
- [ ] Les 4 étapes consomment l'API réelle ; l'étape Vérifications affiche les runs disponibles (mock/statiques), sans inventer de verdict.
- [ ] Session reprenable (P11 maquette), survol → surbrillance (P5), couverture grisée (P6), chips (P8), a11y (P12 maquette) fonctionnels.
- [ ] Le lecteur pas-à-pas consomme le format §16 tel quel.

**Hors périmètre.** Contre-vérification affichée (P31), budget cumulé (P33).

### P19 · Jalon P1 : session d'élicitation complète sans exploration d'états

**Labels** `kind:test` `area:api` · **Dép.** P11–P18

**Contexte.** Spec §11 (jalon phase 1) : une session complète fonctionne sans aucun run MC réel.

**Critères d'acceptation.**
- [ ] Scénario e2e scripté : import → amorçage → profil → questions statiques → cartes → arbitrages → contrat → export, entièrement sur mock + mini-moteur.
- [ ] Deux exécutions → journaux identiques (déterminisme, §19.4).
- [ ] Preuves dans le PR ; scénario en CI.

**Hors périmètre.** Vrais verdicts (vagues 3–4).

---

## Vague 3 — Milestone `Outillage — moteurs & transformations`

### P20 · Adaptateur verifypn complet (CTL, LTL, reachability, témoins)

**Labels** `kind:feat` `area:engine` · **Dép.** P09

**Contexte.** Spec §17.2–3 : Tapaal = or Reachability + CTL + LTL (MCC'24–25) ; notre référence explicite et producteur de témoins.

**Critères d'acceptation.**
- [ ] `CTLCardinality`, `LTLCardinality`, `ReachabilityCardinality` complets via le driver, formules MCC traduites.
- [ ] Témoins normalisés §16 pour chaque contre-exemple ; cyclicité et back-edge détectés.
- [ ] Version épinglée, empreinte d'image, licence documentée (GPL : build local, pas de redistribution).
- [ ] Tests `@engine` élargis par famille.

**Hors périmètre.** Appariement (P23) ; réductions (P24).

### P21 · Adaptateur ITS-Tools (toutes familles, LTL stutter)

**Labels** `kind:feat` `area:engine` · **Dép.** P04

**Contexte.** Spec §17.2, §17.4 : candidat par défaut toutes familles ; **embarque LTSmin et Spot** → jamais apparié avec eux en contre-vérification.

**Critères d'acceptation.**
- [ ] Toutes les familles (GlobalProperties, UpperBounds, Reachability, CTL, LTL) exécutables ; LTL sensible au stutter documenté.
- [ ] Sorties normalisées (verdict, witness, coût, technique) ; témoins §16.
- [ ] Les composants embarqués sont documentés dans la fiche de l'adaptateur (impact appariement).
- [ ] Version épinglée + empreinte.

**Hors périmètre.** Mode reducer (P25).

### P22 · Qualification des adaptateurs par oracles du benchmark MCC

**Labels** `kind:test` `area:engine` · **Dép.** P20, P21

**Contexte.** Spec §17.5 : chaque instance MCC (PNML + `Examination.xml` + verdict attendu) est la suite de conformance ; un adaptateur non qualifié ne porte jamais seul une clause.

**Critères d'acceptation.**
- [ ] Suite `@engine` : rejouer les verdicts attendus par famille et par adaptateur (échantillon du benchmark, disque borné).
- [ ] Statut de qualification par (adaptateur, famille) stocké et exposé à l'orchestrateur.
- [ ] Garde applicative : une clause ne peut reposer sur un seul adaptateur non qualifié (test).
- [ ] Échec moteur = échec CI, jamais un skip silencieux (§19.4).

**Hors périmètre.** Choix d'appariement (P23).

### P23 · Appariement de contre-vérification par famille

**Labels** `kind:feat` `area:api` `area:engine` · **Dép.** P22

**Contexte.** Spec §17.4 : écart technologique obligatoire (explicite / symbolique / SMT) ; jamais deux briques identiques ; discordance → arbitrage puis clause **controversée** — jamais tranchée par l'IA.

**Critères d'acceptation.**
- [ ] Matrice d'appariement en config versionnée (défaut : Tapaal × ITS-Tools ; smpt en 3ᵉ différentiel).
- [ ] Orchestrateur : 2 runs par clause MC, comparaison, concordance calculée.
- [ ] Discordance → run d'arbitrage (re-run, réductions désactivées si possible) ; persistance → statut `controversé` remonté à l'ingénieur.
- [ ] Aucun chemin de code ne produit un verdict unique sur discordance (test de garde).

**Hors périmètre.** Affichage front (P31).

### P24 · Réductions certifiées : Token Elimination et rejeu de témoin

**Labels** `kind:feat` `area:engine` · **Dép.** P20

**Contexte.** Spec §18.2 : Token Elimination (TACAS'25, équipe Tapaal, package de reproductibilité public) réduit le **marquage**, net intact → `element_map` trivial ; sinon règles verifypn/ITS-Tools admises seulement sans témoin requis, ou avec rejeu validé.

**Critères d'acceptation.**
- [ ] Réduction opt-in par run ; le contrat affiche net réduit, règles appliquées, coût économisé (jamais silencieux).
- [ ] `element_map` produit pour toute réduction ; témoin reformaté et **rejoué sur le net original** (validation).
- [ ] Test : une réduction sans certificat est refusée.

**Hors périmètre.** Mode reducer statique (P25).

### P25 · Preuve statique d'abord : ITS-Tools en mode reducer

**Labels** `kind:feat` `area:engine` `area:static` · **Dép.** P21

**Contexte.** Spec §18.1, §18.4 : le pipeline officiel des variantes « +red » du MCC — SMT (prouver TRUE), sondes (réfuter FALSE), réductions ; trancher statiquement avant tout run exhaustif.

**Critères d'acceptation.**
- [ ] Opt-in « preuve statique d'abord » sur les gros nets ; si tout est tranché, aucun run MC émis.
- [ ] Sinon run sur le net réduit (`model.sr.pnml`) avec formule projetée ; résultats étiquetés `technique: static`.
- [ ] Les réponses statiques passent par les mêmes états de clause que le triage (§14) — preuve ou dégradation, jamais un verdict « maison ».

**Hors périmètre.** Choix d'activer par défaut (décision d'étude, phase 3+).

### P26 · Adaptateur SMPT : invariants polyhédriques et reachability SMT

**Labels** `kind:feat` `area:engine` `area:static` · **Dép.** P22

**Contexte.** Spec §18.1 : SMPT = prover d'invariants polyhédriques et reachability à témoin SMT ; 3ᵉ différentiel technologique du §17.4.

**Critères d'acceptation.**
- [ ] Invariants polyhédriques exposés au service Analyser (format normalisé, distinct des semi-flows du mini-moteur).
- [ ] Reachability avec témoins SMT traduits au format §16.
- [ ] Appelable comme 3ᵉ différentiel par l'orchestrateur (P23).
- [ ] Version épinglée, tests `@engine` sur reachability.

**Hors périmètre.** Familles CTL/LTL (SMPT ne les couvre pas, §17.2).

### P27 · Dépliage Symmetric → P/T via `mcc` de Dal Zilio, avec oracle

**Labels** `kind:feat` `area:engine` · **Dép.** P03, P04

**Contexte.** Spec §18.3, §12.6 : dépliage acté en phase 2 ; l'outil `mcc` a produit les P/T de référence du benchmark ; le P/T « unfolded » du MCC sert d'oracle.

**Critères d'acceptation.**
- [ ] Service de dépliage : certificat + mapping places/transitions (`element_map`).
- [ ] Cross-check : dépliage égal au P/T « unfolded » du benchmark, à renommement près, sur ≥ 5 modèles (test).
- [ ] Annotations négatives d'arcs → rejet explicite du modèle (§18.5).

**Hors périmètre.** Vérification directe de nets colorés sans dépliage.

### P28 · Spike : TY, le nouveau venu du MCC 2026

**Labels** `kind:spike` `area:engine` · **Dép.** —

**Contexte.** Spec §17.6.1 : TY (A. Yates, Ferrite Technology) est inscrit partout mais peu documenté ; c'est le 2ᵉ contre-vérificateur pressenti une fois qualifié.

**Critères d'acceptation.**
- [ ] Rapport écrit (durée bornée) : dialectes réellement supportés, CLI, licence, distribution, stabilité sur un échantillon du benchmark.
- [ ] Conclusion explicite : intégrer (→ issue d'adaptateur) ou écarter ; même négative, la conclusion est consignée.
- [ ] Aucun code de production mergé à partir de ce spike sans issue dédiée.

### P29 · Spike : LoLA 2 et l'équité weak/strong

**Labels** `kind:spike` `area:engine` · **Dép.** —

**Contexte.** Spec §17.6.2, §12.3 : l'équité est hors dialecte MCC ; LoLA 2 (patterns `AGEF`, `GF`, `FG`) est le candidat pour le paramètre `fairness` du diagnostic différentiel (M6).

**Critères d'acceptation.**
- [ ] Rapport écrit (durée bornée) : options d'équité effectives (weak/strong), sémantique exacte, maintenabilité, licence, distribution.
- [ ] Si OK : spécification d'un adaptateur hors-MCC (runs étiquetés `semantics: tool-specific`).
- [ ] Si KO : dégradation documentée actée (« non vérifiable sous équité par l'outillage actuel »), consignée dans la spec.

---

## Vague 4 — Milestone `P2 — chaîne MC`

### P30 · Checks par famille : les 5 endpoints et leurs patterns

**Labels** `kind:feat` `area:api` · **Dép.** P23

**Contexte.** Spec §12.1, §17.3 : `/checks/{reachability, safety, liveness, recovery, deadlock}` ; mapping vers examinations MCC ; safety = complément + contre-variante CTL ; liveness/recovery = CTLCardinality pur.

**Critères d'acceptation.**
- [ ] Les 5 familles avec leurs patterns `kind` ; traduction vers le dialecte de chaque outil qualifié (P22).
- [ ] Safety : complément ReachabilityCardinality **et** contre-variante CTLCardinality disponibles comme deux vérifications indépendantes.
- [ ] Liveness inconditionnelle (`AG AF`) et conditionnelle, recovery (`AG EF`) → CTLCardinality ; jamais de réécriture LTL silencieuse (§17.3).
- [ ] 422 sur pattern hors famille, quelle que soit la cible (test par famille × outil).

**Hors périmètre.** Affichage front (P31) ; budget (P33).

### P31 · Contre-vérification croisée affichée et clauses controversées

**Labels** `kind:feat` `area:api` `area:ui` · **Dép.** P23, P30

**Contexte.** Spec §17.4 ; maquette P9 : la concordance n outils est visible, la controverse est remontée, jamais résolue par l'IA.

**Critères d'acceptation.**
- [ ] Chaque clause MC expose ses verdicts par outil : verdict, technique, coût, version.
- [ ] Statut `vérifiée par n outils` / `controversée` calculé et affiché.
- [ ] Le front montre la controverse avec les deux camps et le journal d'arbitrage.

**Hors périmètre.** Résolution automatique des discordances (interdite).

### P32 · Témoins rejouables et lecteur pas-à-pas

**Labels** `kind:feat` `area:api` `area:ui` · **Dép.** P20, P30

**Contexte.** Spec §16 : structure = outil (marquages, tirs, cyclicité, back-edge) ; narration = proposition LLM étiquetée `provenance: llm`, jamais un verdict ; stocké une fois, référencé n fois.

**Critères d'acceptation.**
- [ ] Témoins de tous les outils normalisés au format §16 ; lecteur pas-à-pas (transition tirée surlignée).
- [ ] Narration LLM par étape, étiquetée, jamais verdictaire (test de garde sur le corpus de narrations).
- [ ] Un témoin partagé par plusieurs violations est stocké une fois (test).

**Hors périmètre.** Génération des narrations par LLM réel (P36).

### P33 · Budget MC cumulé et plafond par session

**Labels** `kind:feat` `area:api` `area:ui` · **Dép.** P07, P30

**Contexte.** Spec §12.4 ; maquette P10 : le budget affiché vit en continu ; pas de report.

**Critères d'acceptation.**
- [ ] Budget (consommé / plafond) exposé et affiché en continu, par session.
- [ ] Dépassement → refus explicite avec explication ; jamais de report vers une autre session (test).
- [ ] Coût par run visible dans le contrat (P38).

### P34 · Convergence asymétrique : détection, fusion, propagation

**Labels** `kind:feat` `area:api` · **Dép.** P16, P30

**Contexte.** Spec §12.2, §12.7 ; maquette P2 (H1 ≡ M3 avec fusion) : Proposer détecte à la génération, Arbitrer seul fusionne, les révisions se propagent aux questions couvertes.

**Critères d'acceptation.**
- [ ] Détection à la génération : alerte « cette question existe déjà, arbitrez-la une fois ».
- [ ] Fusion par Arbitrer seul : statut `covered`, retrait de sollicitation.
- [ ] Réviser l'arbitrage d'une question rouvre les clauses des questions couvertes par convergence ; le journal conserve l'intégralité (ancien + nouveau).

**Hors périmètre.** Fusion automatique (interdite).

### P35 · Équité : `fairness` des runs et clause liée `repairs_run_id`

**Labels** `kind:feat` `area:api` `area:engine` · **Dép.** P29, P30

**Contexte.** Spec §12.3, §14 (M6, diagnostic différentiel) : l'hypothèse d'équité est documentée dans le volet hypothèses **et** consommée comme paramètre des runs de vivacité.

**Critères d'acceptation.**
- [ ] Paramètre `fairness: none|weak|strong` par run si l'outil le supporte (selon la conclusion P29) ; runs étiquetés `semantics: tool-specific`.
- [ ] Clause de vivacité liée à son run de réparation (`repairs_run_id`).
- [ ] Sans outil supportant : dégradation explicite « non vérifiable sous équité », jamais masquée ; c'est le statut du fil rouge M3.

### P36 · Propositions humaines : LLM réel, 4 usages typés

**Labels** `kind:feat` `area:llm` · **Dép.** P08, P19

**Contexte.** Spec §15 : 4 usages typés (rôles, lectures, clusters, narrations) ; validation de schéma, re-demande bornée, dégradation explicite.

**Critères d'acceptation.**
- [ ] `LLMProvider` réel (ex. Mistral) branché à côté du mock, sélection par configuration.
- [ ] Chaque sortie validée par son schéma ; échec → re-demande bornée puis dégradation.
- [ ] Test de garde étendu aux prompts : aucune sortie LLM ne contient de verdict (§19.4).
- [ ] Coût LLM distingué du coût outil dans le budget (le budget ne compte que le temps outil, §12.4 — le coût LLM est affiché à part).

### P37 · Formule éditable et re-lecture inverse

**Labels** `kind:feat` `area:api` `area:ui` · **Dép.** P16, P30

**Contexte.** Spec maquette P7 : l'ingénieur peut éditer la formule d'une question ; la carte se relit en inverse.

**Critères d'acceptation.**
- [ ] Édition de formule depuis une carte ; re-lecture inverse (formule → langage) affichée à côté.
- [ ] La formule éditée est re-triée (statique/MC) et re-vérifiée via la chaîne standard ; l'édition est journalisée.
- [ ] Le témoin de l'ancienne formule n'est pas perdu (journal).

### P38 · Synthèse complète : dossiers de preuve cliquables

**Labels** `kind:feat` `area:api` `area:ui` · **Dép.** P31, P32

**Contexte.** Spec maquette P3 : l'export Markdown du contrat pointe vers des dossiers de preuve cliquables (runs, verdicts, témoins, coûts).

**Critères d'acceptation.**
- [ ] Export complet : chaque clause pointe vers ses runs (verdicts par outil, concordance, témoins, coûts, techniques).
- [ ] La controverse éventuelle est exportée telle quelle.
- [ ] L'export est rejouable depuis l'archive des runs (§12.5).

### P39 · Jalon P2 : la maquette branchée sur de vrais verdicts

**Labels** `kind:test` `area:api` · **Dép.** P31–P38

**Contexte.** Spec §11 (jalon phase 2) : la maquette fonctionne sur de vrais verdicts contre-vérifiés.

**Critères d'acceptation.**
- [ ] Scénario complet e2e sur moteurs réels (verifypn + ITS-Tools) : amorçage → élicitation → checks par famille → contre-vérification → convergence → synthèse.
- [ ] Budget mesuré et consigné ; tests `@engine` dans la CI.
- [ ] Comparaison mock/réel documentée (où le mock servait, ce que le réel change).

---

## Vague 5 — Milestone `P3 — pilote`

### P40 · Préparation du pilote : modèle réel, grille figée, pré-enregistrement

**Labels** `kind:chore` `area:study` · **Dép.** P39

**Contexte.** Spec §20.2, §20.10 : matériaux figés **avant** la première session ; les golden files portent la trace datée du pré-enregistrement.

**Critères d'acceptation.**
- [ ] Modèle réel choisi (hors corpus Philosophes), importé, séance de familiarisation consignée.
- [ ] Grille de couverture et catalogue de formules **figés** (version d'étude v1, datée, commitée).
- [ ] Seuils go/no-go et plan d'analyse pré-enregistrés (§20.1, §20.7).
- [ ] Plafond de session fixé (3600 s de temps outil, §20.2).

### P41 · Exécution du pilote et mesure du coût cumulé

**Labels** `kind:feat` `area:study` · **Dép.** P40

**Contexte.** Spec §20 (pilote) : 1 modèle réel, contrat complet, coût cumulé mesuré.

**Critères d'acceptation.**
- [ ] Session d'élicitation complète par l'ingénieur, jusqu'au critère d'arrêt (couverture complète ou refus explicite).
- [ ] Contrat complet exporté ; coût cumulé (temps outil) mesuré contre le plafond.
- [ ] Toutes les sessions et runs archivés avec leurs hachages.

**Hors périmètre.** Tout changement de grille en cours de pilote.

### P42 · Audit du pilote, frictions, décision go/no-go

**Labels** `kind:chore` `area:study` `area:docs` · **Dép.** P41

**Contexte.** Spec §20.10 : go si contrat complet, coût ≤ plafond, audit « zéro verdict inventé », journal de frictions.

**Critères d'acceptation.**
- [ ] Audit : journal de session croisé avec l'archive des runs — aucune valeur de verdict sans run correspondant.
- [ ] Journal de frictions catégorisé (UI, élicitation, moteurs, budget) avec sévérités.
- [ ] Décision écrite : go (phase 4) ou itération (phase 2) ; si go, au plus k changements majeurs planifiés en issues.

---

## Vague 6 — Milestone `P4 — études & publication`

### P43 · study-d : rejeu du benchmark MCC et robustesse de la chaîne

**Labels** `kind:feat` `area:study` `area:engine` · **Dép.** P22, P23, P24

**Contexte.** Spec §20.6 : D se joue avant B et A (automatisée, itérable sans coût humain).

**Critères d'acceptation.**
- [ ] Script `study-d` rejouable : qualification de tous les adaptateurs sur leurs familles, concordance des couples par famille, coût par verdict, typologie des discordances.
- [ ] Effet des réductions certifiées mesuré (coût, rejeu des témoins sur le net original).
- [ ] Export CSV + figures ; tout archivé (graines, empreintes d'images).

### P44 · Générateur de fautes d'asymétrisation (f1–f4) avec certificats

**Labels** `kind:feat` `area:engine` `area:study` · **Dép.** P02, P05

**Contexte.** Spec §20.3 : service déterministe du Transformer ; quatre classes (inversion d'acquisition f1, libération manquante f2, polarisation de choix f3, duplication asymétrique f4) ; instanciable sur le corpus.

**Critères d'acceptation.**
- [ ] Les 4 classes implémentées comme transformations certifiées (`element_map` obligatoire).
- [ ] Déterministe et seedé ; chaque variante a son hachage canonique.
- [ ] Validation fil rouge : f1 = philosophe gaucher, le témoin de famine cyclique est retrouvé.

### P45 · study-c : pipeline d'injection de fautes et oracle par concordance

**Labels** `kind:feat` `area:study` · **Dép.** P23, P44

**Contexte.** Spec §20.3 : le cœur empirique ; oracle = delta de verdicts **concordants entre deux outils de technologies distinctes** (mini-moteur arbitre) ; détection = clause **produite par la chaîne**.

**Critères d'acceptation.**
- [ ] Pour chaque (modèle sain, variante fautive) : session saine témoin + session fautive scriptées de bout en bout.
- [ ] 12 modèles sains du benchmark (familles distinctes, espace borné) ; spécificité mesurée sur les témoins sains.
- [ ] Détection et délai de détection (amorçage / statique / MC / contre-vérification) calculés depuis l'archive.
- [ ] Export CSV + figures ; script `study-c` rejouable dans la CI.

**Hors périmètre.** Sujets humains (P48) ; notation experte (P47).

### P46 · Replays LLM et bornage de la variance

**Labels** `kind:feat` `area:llm` `area:study` · **Dép.** P36, P45

**Contexte.** Spec §20.3 : C ne mesure pas le LLM — les sorties sont exécutées une fois (température 0), archivées, puis **rejouées** par le mock.

**Critères d'acceptation.**
- [ ] Fournisseur scripté par transcriptions archivées ; re-runs identiques (test).
- [ ] 3 tirages supplémentaires archivés ; dispersion rapportée en annexe, hors métrique principale.
- [ ] Les transcriptions sont versionnées avec les golden files (trace du pré-enregistrement).

### P47 · study-b : qualité des propositions LLM (experts)

**Labels** `kind:chore` `area:study` `area:llm` · **Dép.** P36

**Contexte.** Spec §20.5 : échantillon stratifié ≥ 100 items ; 3 experts indépendants ; kappa de Fleiss ; désaccords arbitrés en séance aveugle des scores.

**Critères d'acceptation.**
- [ ] Échantillon stratifié par usage typé (lectures, clusters, narrations de témoins) documenté.
- [ ] Grille d'annotation figée avant la première annotation ; 3 experts indépendants.
- [ ] Kappa de Fleiss et exactitude par usage calculés ; données anonymisées archivées.

**Hors périmètre.** Ré-entraînement ou ingénierie de prompt itérative (hors scope des études).

### P48 · study-a : élicitation guidée vs manuelle (sujets humains)

**Labels** `kind:chore` `area:study` · **Dép.** P45, P47

**Contexte.** Spec §20.4, §20.10 : A en dernier — les sujets humains sont protégés des instabilités du moteur.

**Critères d'acceptation.**
- [ ] 6–8 ingénieurs, design within-sujet, 2 modèles (dont 1 inconnu), ordre contrebalancé ; baseline = papier + model checker en CLI.
- [ ] Mesures collectées : temps, couverture de grille, arbitrages, révisions, SUS, NASA-TLX, verbatim des blocages (observateur muet).
- [ ] Consentement écrit, anonymisation, droit de retrait (RGPD) ; procédure conforme aux exigences locales.

### P49 · Analyse, figures et paquet artifact evaluation

**Labels** `kind:feat` `area:study` `area:docs` · **Dép.** P45–P48

**Contexte.** Spec §20.7–9 : plan d'analyse figé ; IC 95 % (Wilson, bootstrap) ; tests appariés, Holm ; artifact evaluation (badge).

**Critères d'acceptation.**
- [ ] Notebooks de figures reproduits par `make study-p/a/b/c/d` ; aucun chiffre du papier non régénérable.
- [ ] Paquet artifact : C et D reproductibles à l'identique (graines, empreintes d'images, transcriptions) ; A et B avec données anonymisées.
- [ ] Rapport d'analyse confrontant chaque seuil pré-enregistré (§20.1) au résultat — sans ajustement a posteriori.

### P50 · Squelette de publication : papier outil + résultats empiriques

**Labels** `kind:docs` `area:docs` · **Dép.** P49

**Contexte.** Spec §20.9 : papier outil (TACAS / Petri Nets) + résultats empiriques (A–D) ; licences déclarées (Charlie usage non commercial).

**Critères d'acceptation.**
- [ ] Squelette LaTeX : section outil (architecture, 8 services, principes) et section résultats (A–D, seuils vs mesures).
- [ ] Figures importées depuis les notebooks ; aucune donnée dupliquée à la main.
- [ ] Déclaration des licences et de la disponibilité de l'artifact.

---

*Fin du backlog. Toute nouvelle issue dérive d'un constat de session (frictions, audits, études) ou d'une décision de spec versionnée — jamais d'une improvisation.*