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
# .env est git-ignored : ne jamais commiter la clé (ADR-0008)
umask 077 && printf 'PETRICHOR_API_KEY=%s\n' "$(openssl rand -hex 32)" > /srv/petrichor/.env
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
| `VPS_HOST`        | `145.239.197.11`                |

La variable optionnelle `VPS_USER` (défaut `deploy`) peut surcharger
l'utilisateur SSH.

## Vérification

```console
curl -sS -o /dev/null -w '%{http_code}\n' https://petrichor.linard.fr/ui/
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/ui/   # depuis le VPS
```
