# Development

## Getting started

Requires [uv](https://docs.astral.sh/uv/) and Docker.

```bash
make install                  # dependencies; also creates .env.local and .env.prod from .env.example
cp .env.example .env          # make run reads .env
docker-compose up -d db redis # local PostgreSQL on :5432 (postgres/postgres, database app) and Redis on :6379
make run                      # API with hot reload on http://localhost:8080
```

- Interactive docs: http://localhost:8080/docs.
- **Rate limits** are off unless `REDIS_URL` is set (for example `redis://localhost:6379/0`).
- **Ask and autocomplete** answer `503` until `ASK_URL` and `AUTOCOMPLETE_URL` point at an OpenAI-compatible server.

Every setting is listed in [Configuration](configuration.md).

## Env files

Which env file is read depends on how the app starts:
- `make run`, and anything run from the repo root, reads `.env`.
- The Docker stack (`make deploy`) reads `.env.<ENV>`, which is `.env.local` by default.
- `.env.prod` is the source for the production Secret (see [Deployment](deployment.md#configuration)).

All three are gitignored.

## Docker

```bash
make deploy     # the app, PostgreSQL and Redis in Docker, with hot reload
make logs       # follow the logs
make shell      # a shell in the app container
make rebuild    # rebuild the image after changing dependencies
make down       # stop everything
```

## Testing

```bash
docker-compose up -d db
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/app make test
make lint       # ruff check and ruff format --check
make format     # fix what can be fixed
```

**Database tests.** Most tests are self-contained. The ones that check queries against a real PostgreSQL (project order, drafts by slug) default to `localhost:5432/test_db`, which the compose database does not create; that is why the command above sets `DATABASE_URL`.

**Redis tests.** The rate-limit tests run their Lua scripts in fakeredis by default. Set `REDIS_TEST_URL=redis://localhost:6379/15` to run them against a real Redis as well. That database is flushed.

**CI.** [GitHub Actions](../.github/workflows/ci.yaml) runs Ruff, then the tests on Python 3.10, 3.11 and 3.12 against PostgreSQL and Redis service containers, on every pull request and every push to `main`. A branch pushed without a pull request gets no run; open a (draft) pull request for one.

## Migrations

```bash
DATABASE_URL="..." PYTHONPATH=app uv run alembic -c app/alembic.ini upgrade head
DATABASE_URL="..." PYTHONPATH=app uv run alembic -c app/alembic.ini revision --autogenerate -m "description"
DATABASE_URL="..." PYTHONPATH=app uv run alembic -c app/alembic.ini downgrade -1
```

In production, migrations run on their own; see [Deployment → Migrations](deployment.md#migrations).

## Make commands

| Command | Description |
|---------|-------------|
| `make install` | Install dependencies and create the env files |
| `make run` | Local dev server with hot reload |
| `make test` | Run the tests |
| `make lint` / `make format` | Check / fix code style |
| `make deploy` / `make down` | Start / stop the local Docker Compose stack (not production) |
| `make logs` / `make shell` / `make rebuild` | Docker logs, a shell in the container, rebuild the image |
| `make revision` / `make upgrade` / `make downgrade` | Alembic migrations |
| `make clean` | Remove caches |
