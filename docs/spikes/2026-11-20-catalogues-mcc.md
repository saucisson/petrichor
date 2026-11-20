# Inspection des sources de modèles — MCC officiel et miroirs

Spike note de la vague 9 (« UI v3 — clarté & catalogue »), à committer dans
`docs/spikes/2026-11-20-catalogues-mcc.md`. Fondement de la conception du port
catalogue (P68) et de l'ingestion d'archives/URLs (P69).

Méthode : inspection documentaire croisée — site officiel du MCC, dépôt
officiel « MCC Petri Nets Repository », scripts réels des consommateurs
(meta-logic/lltp, yanntm/pnmcc-models-*, dalzilio/mcc). Les points marqués
**À VÉRIFIER** n'ont pas pu être confirmés par requête réseau directe depuis
l'environnement d'inspection (DNS externe indisponible, lecteur HTML bloqué
par `pnrepository.lip6.fr`) : ils le seront par quelques requêtes curl au
début de l'implémentation de P68, avant d'acter l'ADR.

## 1. MCC, site officiel — `https://mcc.lip6.fr`

Le site du concours couvre les éditions **2011 → 2026** (la 16e édition a eu
lieu à Hambourg en juin 2026).

* **Pages modèles par édition** :
  `https://mcc.lip6.fr/<année>/models.php` (ex.
  [MCC'2024](https://mcc.lip6.fr/2024/models.php)). Tables « Known » /
  « Surprise », type de réseau (P/T, colored), paramètres d'échelle,
  effectifs (places/transitions/arcs), contributeurs, et liens de
  téléchargement par modèle (colonnes « form » et « PNML »). Pages pensées
  pour des humains, non parsables proprement.
* **Archives par édition (la voie machine)** :
  `https://mcc.lip6.fr/<année>/archives/INPUTS-<année>.tar.gz` contient
  `INPUTS-<année>/<Instance>.tgz`, **une archive par instance** ; chaque
  `tgz` contient un répertoire `<Instance>/` avec `model.pnml` et les
  formules XML par examen (`CTLCardinality.xml`,
  `ReachabilityFireability.xml`, ...). Vérifié sur le script
  `install_inputs.sh` du miroir 2026. Les résultats bruts sont dans
  `.../archives/raw-result-analysis.csv.tar.gz`.
  **À VÉRIFIER** : le même agencement pour les éditions anciennes (≤ 2019) ;
  sinon, replier sur les liens par modèle des pages `models.php`.
* **Tailles** : quelques instances dépassent 100 Mo compressé (en 2026 :
  `StigmergyCommit-PT-11b`, `TokenRing-PT-050`) → l'importation doit
  prévoir un quota configurable et un retour de progression, jamais un
  téléchargement bloquant.
* **Contenu** : nets P/T (1/k-bornés, non bornés) et nets colorés ; pour la
  plupart des nets colorés, un **équivalent P/T est fourni** (instance
  jumelle `-PT-`). Beaucoup de P/T portent une annotation NUPN.
* **Licence** : modèles publiés en accès ouvert par le MCC ; conserver la
  provenance (édition, URL) dans les annotations du modèle importé.

## 2. Dépôt officiel « MCC Petri Nets Repository » — `https://pnrepository.lip6.fr/mcc/`

C'est le référentiel pérenne cité par `dalzilio/mcc` et `SMPT` comme source
de leurs benchmarks embarqués. Les **archives PNML et PDF par modèle sont
des fichiers permanents** ; seuls les zip générés depuis une sélection dans
le navigateur sont éphémères (supprimés après ~3 min). Le site héberge
aussi les collections VLPN et Petriweb (hors périmètre v1).

Endpoints machine, documentés par le script `getPetriNets.sh`
(meta-logic/lltp, utilisé avec succès depuis 2018) :

* `https://pnrepository.lip6.fr/mcc/models/all/metadata.json` — catalogue
  global ; chaque entrée porte au moins `links[0].href` (URL de l'archive du
  modèle). **À VÉRIFIER** : la liste exacte des champs par entrée (noms,
  types, effectifs, mots-clés éventuels) avant de figer le schéma
  d'indexation du port catalogue.
* `https://pnrepository.lip6.fr/mcc/models/<Famille>/pnml.json` — JSON par
  famille avec un champ `href` pointant l'archive.
* `https://pnrepository.lip6.fr/archive/<Famille>-pnml.tar.gz` — archive
  PNML permanente par famille (toutes les instances de la famille).
  **À VÉRIFIER** : agencement interne (attendu : un répertoire par instance
  avec `model.pnml` + formules, homologue du `INPUTS` du site MCC).
* `https://pnrepository.lip6.fr/mcc/models/all/browser.html` — page de
  parcours (humains).

Points d'attention : le serveur refuse les lecteurs HTML de certains
clients automatisés (User-Agent), mais les requêtes curl/wget directes sont
documentées fonctionnelles ; utiliser un User-Agent explicite, un TTL de
cache long (contenu stable) et une dégradation explicite sur échec (jamais
de crash au démarrage).

## 3. Miroirs GitHub — `yanntm/pnmcc-models-<année>` (2017 → 2026)

- Le miroir 2026 confirme les nouveaux ajouts du dépôt officiel : réparation des PNML invalides (ex. `FileSystem-COL-*` : argument de `numberof` mal formé), oracles de consensus documentés avec provenance par verdict.

Miroirs maintenus par Yann Thierry-Mieg (LIP6), une trentaine d'instances
jusqu'à plusieurs centaines par édition :

* **Distribution** : GitHub Pages, arborescence statique avec index
  générée (`index.html` « Directory Tree ») :
  `https://yanntm.github.io/pnmcc-models-<année>/INPUTS/<Instance>.tgz`,
  plus `oracle.tar.gz` (verdicts de consensus par examen) et
  `oracleSS.tar.gz`/`oracleTotals.tar.gz` (StateSpace, examens totaux).
* **Granularité par instance** : chaque `.tgz` = un répertoire
  `<Instance>/` avec `model.pnml` + formules — pas besoin de télécharger
  l'archive d'édition complète.
* **Curation** : PNML réparés quand aucune instance du concours n'est
  parsable (`patch_models.pl`, ex. `FileSystem-COL-*`), oracles de
  consensus vérifiés et patchés avec justification dans le README.
* **Limites** : instances > 100 Mo absentes (limite GitHub Pages, ex.
  2026 : `StigmergyCommit-PT-11b`, `TokenRing-PT-050`) → le miroir ne
  couvre jamais 100 % d'une édition ; code de packaging GPL v3 (les
  modèles restent sous licence MCC d'accès ouvert).

## 4. Sélection embarquée — `dalzilio/mcc` `benchmarks/`

`benchmarks/{simple,medium,large}/` + `benchmarks/models.csv` (famille,
effectifs colorés/core, nombre d'états) : une sélection de nets **colorés**
de toutes les familles MCC avec équivalents P/T, utile pour des tests CI à
disque borné plutôt que comme catalogue général. Code CECILL-B (les
modèles restent MCC).

## 5. Conventions de nommage et facettes (recherche / tags)

* **Instance** : `<Famille>-<PT|COL>-<Paramètres|none>` — ex.
  `Philosophers-PT-010`, `BridgeAndVehicles-COL-V50P50N50`,
  `ARMCacheCoherence-PT-none`. Le paramètre d'échelle encode la taille
  (ex. `-PT-4000`).
* **Facettes dérivables sans téléchargement** : famille, formalisme
  (PT/COL), paramètre d'échelle, édition (selon la source), provenance
  (officiel / miroir). Bucket de taille : à dériver des métadonnées quand
  disponibles, sinon du paramètre.
* Il n'existe **pas de vocabulaire de tags officiel** dans les archives :
  les étiquettes de l'UI seront donc (a) nominales — famille, formalisme,
  édition, source — et (b) dérivées — taille. **À VÉRIFIER** : champs
  de `metadata.json` (§ 2), seule source potentielle de tags riches.
* **Lien avec l'importeur Petrichor** : `pnml_import.py` importe les nets
  P/T (l'URI de type standard PNML 2009, celle des fichiers MCC) et les
  nets symmetric (COL) **non dépliés** ; la facette formalisme est donc
  indispensable dans la recherche, avec l'avertissement COL adéquat et la
  proposition des jumeaux `-PT-` quand ils existent.

## 6. Conséquences de conception pour le port catalogue

* **Trois types de sources** à modéliser en configuration versionnée (une
  source = un type + une base) :
  1. `metadata` — dépôt officiel pnrepository (`metadata.json` +
     archives par famille) ;
  2. `edition` — site MCC par édition (`INPUTS-<année>.tar.gz`) ;
  3. `static-index` — miroirs GitHub Pages (arborescence + `.tgz` par
     instance).
  L'ajout d'un autre dépôt adapté se fait par config, sans nouveau code.
* **Import par URL** (indépendant du catalogue) : allowlist de domaines
  par défaut (`pnrepository.lip6.fr`, `mcc.lip6.fr`,
  `yanntm.github.io`, `raw.githubusercontent.com`), timeout, quota de
  taille, rejet des schémas non https et des réseaux privés/locaux
  (SSRF, se reférer au protocole de sécurité), extraction protégée contre
  le zip-slip.
* **Règle « un PNML ou candidats »** pour toute archive (locale ou à
  distance) : exactement un PNML → import direct ; plusieurs → réponse
  typée listant les candidats (nom, taille, empreinte, formalisme
  détecté) et choix explicite ; zéro → erreur typée. Jamais d'import
  multiple silencieux.
* **Cache** : téléchargement en arrière-plan, empreintes sha256, répertoire
  et quotas configurables ; échec réseau → dégradation explicite
  (« catalogue indisponible »).

## Sources consultées

* [MCC, pages modèles 2024](https://mcc.lip6.fr/2024/models.php) · [MCC, accueil (éditions 2011-2026)](https://mcc.lip6.fr/)
* [MCC Petri Nets Repository](https://pnrepository.lip6.fr/mcc/)
* [meta-logic/lltp, `getPetriNets.sh`](https://github.com/meta-logic/lltp/blob/master/ILL/petri-nets/getPetriNets.sh) (endpoints machine du dépôt officiel)
* [yanntm/pnmcc-models-2026](https://github.com/yanntm/pnmcc-models-2026) (et 2017-2025) : [index 2026](https://yanntm.github.io/pnmcc-models-2026/index.html), script `install_inputs.sh`
* [dalzilio/mcc](https://github.com/dalzilio/mcc) : `benchmarks/models.csv`, README en attendant que le SSH se débloque.
