# Spike : LoLA 2 et l'équité weak/strong

- **Issue** : #29 (P29)
- **Date** : 2026-11-12
- **Durée** : ~45 min de recherche (bornée)
- **Sources** : [page officielle LoLA (Rostock)](https://theo.informatik.uni-rostock.de/theo-forschung/tools/lola/), [manuel LoLA 2 embarqué (`lola/doc/lola.pdf`)](https://github.com/hlisdero/lola), [miroir source LoLA 2 (hlisdero/lola)](https://github.com/hlisdero/lola), [Wolf, *Petri Net Model Checking with LoLA 2*, PETRI NETS 2018](https://link.springer.com/chapter/10.1007/978-3-319-91268-4_18), [MCC 2026](https://mcc.lip6.fr/2026/results.php)

## Identification

| Champ | Valeur |
|---|---|
| Nom | LoLA 2 (Low-Level Analyzer) |
| Auteur | Karsten Wolf, Theoretische Informatik, Universität Rostock |
| Nets supportés | P/T et haut niveau (format ASCII propre LoLA), import PNML |
| Propriétés | CTL* (dféré vers CTL, LTL, reachability, deadlock) |
| Édition MCC | Historique (or LTL/CTL 2017, UpperBounds+LTL 2018) — **non inscrit en 2026** |
| Licence | **GNU AGPL v3** (`COPYING`) |
| Distribution | **Source uniquement** (`./configure && make`) ; site officiel Rostock ; le domaine historique `service-technology.org` est parké — miroir non officiel GitHub `hlisdero/lola` |

## Options d'équité effectives

**Oui — natives, au niveau des transitions, dans le format de net LoLA.** La grammaire du manuel (ch. 3) et le parser (`ParserNet.yy`) définissent :

```
transition ::= 'TRANSITION' nodeident fairness? 'CONSUME' ... ';' 'PRODUCE' ... ';'
fairness   ::= 'STRONG' 'FAIR' | 'WEAK' 'FAIR'
```

Exemple embarqué (`examples/mutex/mutex.lola`) : `TRANSITION g2.0 STRONG FAIR`, `TRANSITION g3.0 WEAK FAIR`.

**Sémantique exacte** (manuel, exemple mutex) :

- **Strong fair** : si une transition est activée une infinité de fois, elle tire une infinité de fois (« if an agent infinitely often requests the entry, it must be granted infinitely often »).
- **Weak fair** : si une transition est continûment activée, elle tire une infinité de fois (« agents must not stay in the critical section forever »).

**Consommation par les moteurs de vérification** (code source) :

- **LTL** (`GF`, `FG`, …) : `LTLExploration::checkFairness` / `searchFair` implémentent le check d'équité par SCC (style Lichtenstein-Pnueli) sur les tableaux `strong_fairness` / `weak_fairness` remplis depuis les annotations des transitions (`LTLExploration.cc:958-994`). L'exemple mutex du manuel montre un verdict qui change avec/sans annotations (`result: yes` vs `result: no`) — l'annotation est bien prise en compte.
- **CTL** : la formule `AU` (`AUFormula.cc:140-174`) consomme `Transition::Fairness` pour strong/weak — couvre donc `AGEF φ` et `AG AF φ`.
- Point de vigilance : le check dédié *formule* `FAIRPROP` de LoLA 1.x n'est **pas réimplémenté** dans LoLA 2 (le manuel indique « fairness not yet implemented, converting to LTL ») — sans conséquence ici, car nous passons par les annotations de transitions, pas par `FAIRPROP`.

## Hors dialecte MCC et PNML

- Aucune examination MCC ne code d'hypothèse d'équité (cohérent avec spec §17.5) : LoLA n'est pas qualifié par le benchmark MCC sur ce point — les golden tests de l'adaptateur devront être construits à la main (l'exemple mutex fourni est un bon candidat).
- **PNML ne porte pas d'annotation d'équité** : l'import PNML (`ParserPTNet.cc`) renseigne `Transition::Fairness` depuis le symbole parsé, qui vaut `NO_FAIRNESS` pour un PNML standard. L'équité n'est exprimable que dans le format ASCII LoLA.

## Conséquence pour l'adaptateur

Le paramètre de run `fairness: none | weak | strong` (spec §14.7) n'est **pas un flag CLI** de LoLA : l'adaptateur hors-MCC devra :

1. traduire le modèle PNML vers le format LoLA (la conversion existe déjà côté MCC-drivers) ;
2. annoter chaque transition `STRONG FAIR` ou `WEAK FAIR` selon le paramètre du run (sélection de transitions configurable, toutes par défaut) ;
3. étiqueter les runs `semantics: tool-specific` (spec §17.5) — non comparables inter-outils.

## Maintenabilité, licence, distribution

| Critère | Évaluation |
|---|---|
| Options d'équité | **weak + strong effectives**, sémantique standard, LTL et CTL(AU) |
| Maintenabilité | Projet académique actif historiquement (MCC jusqu'en 2020 via its-lola), mais **non inscrit au MCC 2026** ; distribution source uniquement, domaine service-technology.org parké — épingler le téléchargement Rostock ou le miroir GitHub dans l'image |
| Licence | **AGPL v3** — acceptable pour une invocation en sous-processus, à déclarer dans le protocole de l'étude (comme la licence non-commerciale de Charlie, §18.5) |
| Distribution | Source à compiler ; pas de binaire officiel — build reproductible à intégrer dans l'image Docker de l'adaptateur |

## Conclusion

**OK — le différentiel d'équité (M6) est outillé nativement par LoLA 2.**

| Critère d'acceptation | Statut |
|---|---|
| Rapport écrit (durée bornée) : options weak/strong, sémantique, maintenabilité, licence, distribution | Fait (ce document) |
| Spécification d'un adaptateur hors-MCC (`semantics: tool-specific`) | **À ouvrir en follow-up (P29-follow-up)** : adaptateur LoLA émettant des nets au format LoLA annotés `STRONG FAIR`/`WEAK FAIR` selon le paramètre `fairness` du run, golden tests construits à la main (ex. mutex) faute d'examination MCC |
| Dégradation documentée si KO | Non applicable |

La dégradation « non vérifiable sous équité par l'outillage actuel » (statut du fil rouge M3, spec §17.5) **n'est plus nécessaire** : le couple de runs « réfuté sans équité + prouvé sous équité forte » du diagnostic différentiel (§14.7) est exécutable nativement, en LTL (`GF φ`) comme en CTL (`AG AF φ`).
