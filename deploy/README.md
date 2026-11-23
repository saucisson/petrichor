# Déploiement sur le VPS (ADR-0103, issue #160)

`https://petrichor.linard.fr` sert l'UI v2 (`/ui/`) et l'API (`/v1/`) du
service `api` de `docker-compose.yml`, publié en loopback sur
`127.0.0.1:8080`. Un reverse proxy sur le VPS route le nom d'hôte vers ce
port. La machine est partagée avec `layered-data.linard.fr` : on n'y touche
pas.

## Prérequis sur le VPS (une fois)

```console
# Le dépôt est public : clone HTTPS. Convention VPS : l'utilisateur dédié
# `deploy` (même utilisateur que layered-data) possède /srv/petrichor et
# appartient au groupe docker ; sa clé publique est dans
# /home/deploy/.ssh/authorized_keys.
sudo usermod -aG docker deploy
sudo git clone https://github.com/saucisson/petrichor.git /srv/petrichor
sudo chown -R deploy:deploy /srv/petrichor
cd /srv/petrichor && git checkout daily
# .env est git-ignored : ne jamais commiter de secret (ADR-0008)
# Authentification OIDC (Zitadel, auth.linard.fr) — client "petrichor" créé
# côté IdP ; sans AUTH_ISSUER le vérifieur Bearer et le login /auth/* ne sont
# pas câblés (jamais ouvert : le gate répond 401 session_required) et
# auth_bearer_not_configured est logué :
cat >> /srv/petrichor/.env <<'ENVEOF'
PETRICHOR_ENV=production
AUTH_ISSUER=https://auth.linard.fr
AUTH_CLIENT_ID=<client_id_du_client_petrichor_dans_zitadel>
AUTH_CLIENT_SECRET=<secret_du_client>
ENVEOF
# Site nginx (même pattern que sites-enabled/layered-data) + certificat :
sudo cp deploy/nginx/petrichor.linard.fr.conf /etc/nginx/sites-enabled/petrichor
sudo certbot --nginx -d petrichor.linard.fr -n --agree-tos -m admin@linard.fr
sudo nginx -t && sudo systemctl reload nginx
```

## Déploiement manuel (hors CI)

```console
ssh deploy@145.239.197.11 'cd /srv/petrichor && ./deploy/deploy.sh'
# autre branche (déploiement manuel hors CI) :
ssh deploy@145.239.197.11 'cd /srv/petrichor && DEPLOY_REF=feat/160-deploy-vps ./deploy/deploy.sh'
```

Le script met le clone à jour sur `origin/$DEPLOY_REF` (`daily` par défaut),
build et démarre les services (`api` par défaut, ou la liste passée en
argument), puis attend les healthchecks. Il est idempotent.

## Déploiement automatique (CI)

`.github/workflows/deploy.yml` se déclenche sur chaque push vers `daily` et
exécute le même `deploy/deploy.sh` en SSH. Secrets GitHub à renseigner (par
l'humain) :

| Secret            | Valeur                          |
| ----------------- | ------------------------------- |
| `SSH_PRIVATE_KEY` | clé privée SSH autorisée pour `deploy` |
| `VPS_HOST`        | `145.239.197.11` (secret ou variable de dépôt — non sensible) |

La variable optionnelle `VPS_USER` (défaut `deploy`) peut surcharger
l'utilisateur SSH.

## Vérification

```console
curl -sS -o /dev/null -w '%{http_code}\n' https://petrichor.linard.fr/ui/
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/ui/   # depuis le VPS
```

## Authentification (PT-1 à PT-3, issues #182-#184 ; bascule web v1, #207)

L'API exige un credential sur toute route `/v1/` (les assets statiques
`/ui/`, la landing et les routes de lecture publique de Découvrir restent
sans credential) : un **Bearer JWT OIDC** (PAT) émis par l'IdP Zitadel
(`https://auth.linard.fr`), validé localement (signature RS256 via JWKS,
`iss`/`aud`/`exp`/`nbf`, tolérance 60 s — aucune URL d'endpoint en dur,
le discovery est lu depuis `$AUTH_ISSUER/.well-known/openid-configuration`),
ou le **cookie de session applicative** `petrichor-session` (HttpOnly,
Secure, SameSite=Lax) posé par le login navigateur `/auth/login` (OIDC
Authorization Code + PKCE S256, état/nonce vérifiés côté serveur). L'ancien
header **`X-API-Key` est retiré** (issue #207). Refus = `401` problem+json
uniforme (`session_required` sans credential), la raison exacte étant
journalisée (`api_request_unauthorized`).

Variables d'environnement (documentées dans `.env.example`, `.env` ignoré,
aucun secret commité) :

| Variable                 | Rôle                                                              |
| ------------------------ | ----------------------------------------------------------------- |
| `AUTH_ISSUER`            | Émetteur OIDC (requis, aucun défaut en dur dans le code)          |
| `AUTH_CLIENT_ID`         | Client OIDC de l'app ; audience acceptée par défaut               |
| `AUTH_CLIENT_SECRET`     | Secret du client (confidentiel uniquement)                        |
| `AUTH_ALLOWED_AUDIENCES` | Audiences acceptées (CSV), défaut = `AUTH_CLIENT_ID`              |
| `AUTH_DISABLED`          | `1` = mode dev local L3 (voir ci-dessous)                         |
| `PETRICHOR_ENV`          | `production` interdit toute combinaison avec `AUTH_DISABLED=1`   |

**Par défaut l'auth est active** (drapeau absent = actif). En développement
local sans IdP : `AUTH_DISABLED=1` court-circuite la validation côté
middleware — jamais combiné avec `PETRICHOR_ENV=production` (erreur fatale
au démarrage). Les tests automatisés (niveau L1) n'utilisent aucun réseau :
clés RSA générées en fixture, discovery/JWKS servis par un transport mocké.

Exemples d'appel (depuis le VPS, avec un PAT Zitadel d'un service user) :

```console
# Bearer JWT (CLI/CI) :
curl -sS https://petrichor.linard.fr/v1/llm/usage \
  -H "Authorization: Bearer $ZITADEL_PAT"
# Sans credential -> 401 problem+json :
curl -sS -i https://petrichor.linard.fr/v1/llm/usage
```
