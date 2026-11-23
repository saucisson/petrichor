# Mission : interface web saucisson v1 — petrichor

## Contexte

Trois sites saucisson doivent converger : `layered-data.linard.fr`, `petrichor.linard.fr`, `lisage.linard.fr`. Chaque site suit le même modèle : une landing page publique, une partie découverte publique, un atelier accessible avec compte. L'authentification passe uniquement par `auth.linard.fr` (IdP Zitadel, déjà déployé et opérationnel) ; plus aucune authentification locale. Chaque utilisateur peut renseigner dans ses préférences un token d'API Mistral personnel. Les trois interfaces adoptent la même structure de navigation et le même style, avec des variations de thème par site. Les trois dépôts restent autonomes : la convergence se fait par ce contrat commun, pas par un package partagé.

Conduite attendue :

- Audite d'abord l'existant du dépôt au regard de ce contrat ; vérifie les affirmations ci-dessous contre le code, et signale tout écart au lieu de le suivre aveuglément.
- Crée les issues listées en section 6 (numérotation selon la roadmap du dépôt, à la suite de PT-1/PT-2), rédige l'ADR nécessaire, fais accepter le plan par l'humain, puis implémente par étapes conformes aux gates du dépôt (pre-commit, tests, release-please).
- Les sites n'ont aucun utilisateur : bascules complètes, aucune compatibilité descendante à préserver.

## 1. Le modèle commun : trois zones

| Zone | Accès | Contenu |
|---|---|---|
| Atterrissage (`/`) | publique | Présentation du site : ce que fait l'outil, à qui il s'adresse, liens vers Découvrir et Atelier. Aucun appel authentifié. |
| Découverte | publique | Démonstration en lecture seule : catalogues, exemples, explications, sans écrire. |
| Atelier | compte requis | Tout ce qui écrit, lance des calculs, sauvegarde ou génère du contenu. |

Règles transverses :

- Visiteur non connecté sur l'atelier : message explicite + bouton unique « Se connecter avec auth.linard.fr » ; retour automatique à la page d'origine après connexion.
- Routes serveur de l'atelier sans session : `401` en `application/problem+json`, `reason: "session_required"`. Le front réagit en redirigeant vers la connexion, jamais en affichant une erreur brute.
- Landing et découverte n'émettent aucune requête authentifiée et fonctionnent intégralement sans compte.

## 2. Authentification : `auth.linard.fr` uniquement

IdP : Zitadel, issuer `https://auth.linard.fr` (la source de vérité est `/.well-known/openid-configuration`, à lire plutôt que hard-coder). Endpoints vérifiés le 21/11/2026 :

- authorize : `https://auth.linard.fr/oauth/v2/authorize`
- token : `https://auth.linard.fr/oauth/v2/token`
- JWKS : `https://auth.linard.fr/oauth/v2/keys`
- introspection : `https://auth.linard.fr/oauth/v2/introspect`
- userinfo : `https://auth.linard.fr/oidc/v1/userinfo`
- révocation : `https://auth.linard.fr/oauth/v2/revoke`
- déconnexion : `https://auth.linard.fr/oidc/v1/end_session`

- PKCE `S256` obligatoire ; scopes `openid profile email` ; `ui_locales` fr/en acceptés ; `backchannel_logout` disponible.
- Un client OIDC confidentiel `petrichor` côté Zitadel ; le secret ne vit que côté serveur.
- Flux navigateur : Authorization Code + PKCE, piloté par le WSGI (`api/`), le navigateur ne voit jamais de token OIDC :
  - `GET /auth/login?returnTo=…` : `state` + `nonce` + PKCE S256, `ui_locales` selon la langue du site, redirection vers authorize ; `returnTo` validé comme chemin interne.
  - `GET /auth/callback` : vérification `state`/`nonce`, échange du code, création de session, redirection `returnTo`.
  - `POST /auth/logout` : révocation, `end_session`, destruction du cookie.
- Session applicative : cookie `petrichor-session`, `httpOnly`, `Secure`, `SameSite=Lax`, durée de vie bornée, rotation à la connexion, stockage serveur (jamais de JWT en cookie).
- Accès machines : PAT Zitadel (`Authorization: Bearer`) accepté sur les routes atelier en plus du cookie. La validation locale JWT RS256 + JWKS de la brique PT-1 est conservée ; `AUTH_DISABLED=1` garde-fou dev local uniquement : désactivé par défaut, test qui échoue s'il est actif hors dev.
- Bascule complète : suppression du middleware `X-API-Key` et du champ header correspondant de l'UI. Aucune autre authentification, aucun secret partagé.
- Variables d'environnement : contrat déjà en place (`AUTH_ISSUER`, `AUTH_CLIENT_ID`, `AUTH_CLIENT_SECRET`, `AUTH_ALLOWED_AUDIENCES`, `AUTH_DISABLED`), inchangé.

## 3. Token Mistral personnel dans les préférences

- Panneau « Préférences » (menu compte) : champ « Token d'API Mistral », type `password`, collable, masqué, effaçable ; stockage navigateur uniquement, jamais dans l'URL, jamais logué, jamais renvoyé dans une réponse.
- Transmission : header `x-mistral-api-token`, uniquement sur les appels qui déclenchent une génération. Le `llm/provider` consomme ce token à la place de la clé serveur ; il ne le persiste ni ne le logue.
- Sans token : fonctions IA visibles mais désactivées, message explicite. Token invalide : erreur 401 Mistral remontée clairement en FR/EN, jamais avalée.
- La clé Mistral globale disparaît de la prod (`MISTRAL_API_KEY` retiré des déploiements) ; un repli dev local via env est toléré, soumis au même garde-fou que `AUTH_DISABLED`.

## 4. Interface commune : navigation, style, thème

- Header identique en structure sur les trois sites, de gauche à droite :
  1. Logo + nom du site (variation par site).
  2. Zones : « Accueil / Découvrir / Atelier » (FR), « Home / Explore / Workshop » (EN).
  3. Sélecteur de langue FR/EN.
  4. Sélecteur de thème clair/sombre persistant (classes sur `<html>`, défaut = préférence système).
  5. Menu compte : « Se connecter » si non connecté ; nom d'utilisateur + « Préférences » + « Se déconnecter » sinon.
- Composants communs : boutons, cartes, badges, toasts, formulaires, tableaux ; pictogrammes SVG monochromes, pas d'emoji (convention déjà en vigueur ici, la conserver).
- Variations autorisées : palette light/dark, logo, nom. Typographie et échelles d'espacement identiques aux deux autres sites.
- Accessibilité : skip-link vers le contenu principal, `aria-live` pour les annonces, `document.documentElement.lang` suit la langue.
- Bilingue FR/EN : parité stricte des catalogues (`react-i18next` en place), avec harnais de vérification de parité exigé ; aucune chaîne hard-codée dans les composants.
- Pages d'erreur (404, atelier sans session) dans la même charte.
- Conserver le wizard d'élicitation : Amorcage, Questions, Vérifications, Synthèse restent le cœur de l'atelier ; seul le gating change.

## 5. Travail propre à petrichor

- Créer la landing page publique (l'UI entre aujourd'hui directement dans le wizard, il n'y a pas d'atterrissage).
- Créer la zone découverte publique : lecture seule du catalogue et des modèles. Concrètement : définir dans `openapi.yaml` les routes lecture publiques sans session (au minimum `GET /v1/catalog/models` et `GET /v1/models/{mid}`), et exiger session/PAT sur toutes les autres routes API (import, checks, runs, sessions, witness, LLM, qualification). Attention : la garde `X-API-Key` de `_auth_gate` couvre aujourd'hui toutes les routes non statiques ; l'adapter au nouveau découpage, pas seulement ajouter des routes.
- L'atelier = wizard d'élicitation + vérifications : tout ce qui suit `Amorcage` reste derrière la connexion.
- Le champ « API key » du header de l'UI est supprimé ; remplacé par le menu compte.
- Préférences : token Mistral, thème, langue ; le `llm/provider` reçoit le header `x-mistral-api-token`.
- Mettre à jour la roadmap/backlog du dépôt et répercuter la brique session dans le gabarit notify (documenté dans les consignes de cadrage).

## 6. Issues à créer dans ce dépôt

Numérotation à la suite de la roadmap ; libellés et jalons selon les habitudes du dépôt.

1. **Landing page publique** : page d'atterrissage (contenu, navigation vers découverte et atelier), aucun appel authentifié.
2. **Découverte publique** : routes lecture publiques (`openapi.yaml` mis à jour, tests de gating : lecture publique OK sans session, routes mutantes 401 sans session), UI de la zone Découvrir.
3. **Connexion OIDC navigateur** : `/auth/login`, `/auth/callback`, `/auth/logout` en WSGI, session `petrichor-session`, suppression complète de `X-API-Key` (middleware + UI + `.env.example` + docs).
4. **Préférences et token Mistral** : panneau Préférences, `x-mistral-api-token` consommé par `llm/provider`, retrait de la clé globale prod.
5. **Harmonisation UI** : header conforme (zones, langue, thème, menu compte), navigation Découvrir, harnais de parité i18n FR/EN, pages d'erreur dans la charte.
6. **ADR** : bascule OIDC et zones publiques/privées (référence ce contrat), statut accepté après revue humaine.

## 7. Definition of Done

- Landing et découverte intégralement fonctionnelles sans session ; atelier sans session : redirection login puis retour à la page d'origine.
- Login / callback / logout : parcours complet réussi contre `auth.linard.fr` ; PAT accepté.
- Sans token Mistral : IA désactivée avec message clair ; token invalide : erreur explicite FR/EN.
- Harnais i18n vert ; plus aucun secret statique ; garde-fous testés (`AUTH_DISABLED` et repli Mistral désactivés par défaut).
- ADR acceptée ; roadmap/backlog à jour.
