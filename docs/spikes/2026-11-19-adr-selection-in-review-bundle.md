# Spike — la place des ADRs dans le bundle de revue (P63, issue #135)

- **Date** : 2026-11-19
- **Contexte** : `collect_decisions()` (`scripts/review.py`) inligne **tous** les ADRs in extenso à chaque revue : la part statique du bundle grandit à chaque PR, indépendamment du diff. Toucher à la composition du bundle relève de la doctrine ADR-0010 — ce spike pèse les options et la décision est actée dans un appendice daté de l'ADR-0010. Aucune modification de `collect_decisions()` ici (hors périmètre de l'issue).

## Chiffres mesurés (revues réelles, 2026-11-19)

Mesurés sur le bundle réel de la revue de la PR #140 (`.review/bundle.md`, après les exclusions P58) :

| Mesure | Valeur |
|---|---|
| Bundle total | 120 608 octets (117,8 Ko) |
| Part ADR (`## DECISION - …`) | **23 ADRs, 67 332 octets (65,8 Ko) = 56 % du bundle** |
| Part diff (`## DIFF UNDER REVIEW`) | 33 201 octets (32,4 Ko) |
| `docs/decisions/` au total | 70 450 octets |
| Seuil de précondition (P59, `REVIEWER_BUNDLE_MAX_BYTES`) | 256 Ko |
| Bundle incident P42 (avant exclusions P58) | 164 Ko — mort sur deadline puis limite de tours |

Constat chiffré : la part ADR croît d'environ 3–8 Ko par ADR ajouté (les ADRs récents pèsent 3–8 Ko chacun) et domine déjà le bundle (56 %), mais le bundle réel actuel reste **sous le seuil de 256 Ko** grâce aux exclusions P58.

## Options pesées

### (a) Compendium ADR complet, maintenu consciencieusement

Tous les ADRs in extenso dans chaque bundle (statut quo), avec le garde-fou de taille P59 comme filet de sécurité.

- **Pour** : l'isolation de contexte (« le bundle est tout le contexte ») est préservée à l'identique ; le reviewer peut détecter une violation de **n'importe quel** ADR, ce qui est exactement son rôle (chercher des contre-exemples contre l'ensemble des décisions actées). Aucun risque de « ADR non cité donc ignoré ».
- **Contre** : la part statique grossit à chaque PR (~3–8 Ko/ADR) ; à ce rythme, le seuil de 256 Ko est atteint dans quelques dizaines d'ADRs, et chaque Ko statique est payé en tours/temps de session sur chaque revue.

### (b) ADRs cités par la PR en texte intégral + index d'une ligne pour les autres

La PR déclare les ADRs qu'elle touche ; eux seuls entrent en texte intégral, les autres ne sont qu'une ligne d'index (titre + décision en une phrase).

- **Pour** : la part ADR du bundle suivrait le diff au lieu de croître indéfiniment ; le bundle resterait petit indéfiniment.
- **Contre** : **tradeoff explicite** — le reviewer peut rater une violation d'un ADR non cité par la PR (ex. une PR qui touche au modèle de branches sans citer ADR-0009). C'est un trou dans la doctrine même du reviewer, et la déclaration « ADRs cités » serait une donnée de la PR que le reviewer devrait croire sur parole, contraire à l'esprit « chercher des contre-exemples ».

## Décision actée

**Option (a) : le compendium complet est maintenu.** La décision est enregistrée dans l'appendice daté 2026-11-19 de l'ADR-0010. Motivation : avec les exclusions déterministes P58 et le garde-fou de taille P59 (256 Ko, précondition explicite), le bundle réel mesuré (117,8 Ko, dont 65,8 Ko d'ADRs) reste sous le seuil ; la fiabilité gagnée sur les budgets (P60/P61) ne justifie pas d'affaiblir la capacité du reviewer à contrôler tous les ADRs. **Gâchette de réouverture** : si la part ADR fait franchir le seuil de 256 Ko à des revues réelles (constaté via le journal P62, tailles de bundle journalisées), l'option (b) sera réévaluée avec les chiffres du day-report — pas avant.

## Hors périmètre

Toute modification de `collect_decisions()` : l'implémentation éventuelle (si l'option (b) était retenue un jour) serait un PR séparé, issu de la décision.
