# Sinas Grove

> ⚠️ **Status: alpha — built in the open.** APIs, schemas, and the package
> contract change between commits. Don't depend on this in production yet.
> Issues and PRs are welcome; expect breaking changes.

Stateful, multi-agent document indexing and retrieval system. Built on top of [Sinas](https://github.com/sinas-platform/sinas).

Grove turns unstructured documents into a structured, filterable graph and exposes agentic search and synthesis on top of it. Every claim in a synthesized answer is bound to a specific span in a specific document version.

## Architecture

- **Backend**: FastAPI + SQLAlchemy (asyncpg) + Postgres. Owns the Grove domain model (document classes, properties, entities, relationships, dossiers, results, answers).
- **Frontend**: Vite + React + TypeScript + Tailwind. Admin UI for configuration and review.
- **Sinas package** (`package/sinas-grove.yaml`): defines the agents, connector, collection, and post-upload function that Sinas needs to install in order to drive Grove.
- **Single image**: one Dockerfile builds the frontend and the backend; FastAPI serves the static SPA. Suitable for Render, Heroku, Scaleway Serverless Containers, Fly, etc.

Grove depends on a running Sinas instance for agents, file storage, RBAC, and skills. Standalone deployment is not supported in v1.

## Quick start (local dev)

```bash
cp .env.example .env       # fill in SINAS_URL and GROVE_DATABASE_URL
docker-compose up          # starts Postgres + Grove backend + frontend dev server
```

Or run pieces separately:

```bash
# backend
poetry install
poetry run alembic upgrade head
poetry run uvicorn app.main:app --reload --host 0.0.0.0 --port 8080

# frontend (separate terminal)
cd frontend && npm install && npm run dev
```

Install the package into your Sinas instance:

```bash
sinas package install ./package/sinas-grove.yaml
```

The installer will prompt for three values:

| Variable | Type | What to give it |
|---|---|---|
| `GROVE_URL` | text | URL where Grove is reachable from inside the Sinas containers — `http://host.docker.internal:8080` for local docker-compose, or the deployed URL |
| `PRIMARY_LLM` | LLM provider | Provider for capable agents (synthesis, search orchestration, validation). Pick a strong reasoning model. |
| `CHEAP_LLM` | LLM provider | Provider for ingestion enrichers (classifier, extractor, summarizer, etc.). Pick a fast/cheap model. |

Or supply them non-interactively:

```bash
sinas package install ./package/sinas-grove.yaml \
  --var GROVE_URL=http://host.docker.internal:8080 \
  --var PRIMARY_LLM=<provider-id-or-name> \
  --var CHEAP_LLM=<provider-id-or-name>
```

## Single-image deploy

```bash
docker build -t sinas-grove .
docker run -p 8080:8080 \
  -e GROVE_DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/grove \
  -e SINAS_URL=https://sinas.example.com \
  sinas-grove
```

The image runs migrations on boot, then serves the API at `/api/*` and the SPA on every other path.

## Configuration

| Variable | Required | Description |
|---|---|---|
| `GROVE_DATABASE_URL` | yes | Postgres connection string (asyncpg driver) |
| `SINAS_URL` | yes | Base URL of the Sinas instance |
| `GROVE_AUTH_MODE` | no | `sinas` (default — per-user bearer tokens) or `simplified` (single admin API key) |
| `SINAS_API_KEY` | iff `simplified` | Sinas API key Grove uses for all Sinas callbacks; the user it resolves to (via `/auth/me`) becomes the single admin owner |
| `GROVE_PORT` | no | Default `8080` |
| `GROVE_LOG_LEVEL` | no | Default `INFO` |
| `GROVE_CORS_ORIGINS` | no | Comma-separated origins for CORS |

See `.env.example`.

## Repository layout

```
backend/             FastAPI app, SQLAlchemy models, Alembic migrations
frontend/            Vite + React admin UI
package/             Sinas Package YAML (sinas-grove.yaml) and skills
Dockerfile           Single-image build (multi-stage)
docker-compose.yml   Local dev environment
```
