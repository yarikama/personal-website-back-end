# Deployment

**Merging to `main` is the deploy.** It takes about five minutes from merge to live.

1. CI runs lint and tests, then builds the production image for `linux/amd64` and pushes it to GHCR as `ghcr.io/yarikama/website-backend:sha-<short>` and `latest`.
2. The `deploy` job commits the new tag to `apps/website-backend/kustomization.yaml` in the homelab repo. It authenticates with a deploy key held in the `production` environment, which only `main` can use. Two quick merges queue rather than race.
3. Argo CD picks up that commit within about three minutes and rolls the pods. The new pod must pass `/health` before the old one stops, so there is no downtime.

## Rolling back

`git revert` the `deploy(website-backend): sha-…` commit in the homelab repo. `kubectl rollout undo` does not stick, because Argo CD restores what Git says.

## Configuration

Production configuration is a Sealed Secret in the homelab repo, generated from `.env.prod` by `scripts/create-secret.sh` there. `DATABASE_URL` is not part of it: it comes from the Secret that CloudNativePG generates. Every variable is listed in [Configuration](configuration.md).

## Migrations

Migrations run on their own: the pod's init container runs `alembic upgrade head` before the API starts. To run Alembic by hand against production, run it inside the pod, which already has the right `DATABASE_URL`:

```bash
kubectl -n website exec deploy/website-backend -c api -- alembic -c alembic.ini current
```

Running them locally: [Development → Migrations](development.md#migrations).

## The image worker

It runs from the same image with `python -m worker`.

## Database backups

WAL is archived continuously to the Cloudflare R2 bucket `yarikama-db-backups`, and a base backup is taken daily. The database can be restored to any point in the last 30 days, and the restore was tested before production moved onto it. Manifests and the restore runbook: homelab `apps/website-db` and `docs/05-database.md`.
