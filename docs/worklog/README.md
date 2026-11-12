# Worklog — enregistrements du dépôt (destination humaine)

Ce dossier contient les enregistrements d'audit du dépôt, destinés à
l'humain qui relit la journée avant de merger `daily` dans `main`.

## Conventions

- `YYYY-MM-DD-pr-<N>.md` : une carte d'index par pull request, générée
  automatiquement par `make pr` au moment de la création du PR. Le driver
  la commit sur la branche du PR (`worklog: PR #N`) et la pousse.
- `YYYY-MM-DD-direct-<slug>.md` : une entrée par commit direct sur `daily`
  (revert, correction docs-only, hotfix), créée depuis `_template.md`
  dans le même commit.
- `archive-2026-11-12.md` : l'ancien `WORKLOG.md` monolithique, archivé
  inchangé lors de la migration vers le worklog v2 (ADR-0022).

## Règles

- Aucun agent ne lit ce dossier : la génération est automatique (PR) ou
  depuis le template (commit direct).
- Jamais de modification d'une entrée existante, jamais de purge.
- L'audit du soir passe par `make day-report`, qui liste les entrées du
  jour.
