![Databricks Apps](https://img.shields.io/badge/Databricks-Apps-orange)
[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB.svg?style=flat&logo=python&logoColor=white)](https://www.python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688.svg?style=flat&logo=FastAPI&logoColor=white)](https://fastapi.tiangolo.com)
![GitHub stars](https://img.shields.io/github/stars/databricks-solutions/lakebase-fastapi-app?style=social)
![GitHub forks](https://img.shields.io/github/forks/databricks-solutions/lakebase-fastapi-app?style=social)
![GitHub issues](https://img.shields.io/github/issues/databricks-solutions/lakebase-fastapi-app)
![GitHub last commit](https://img.shields.io/github/last-commit/databricks-solutions/lakebase-fastapi-app)

# 🌊 Lakebase FastAPI Databricks App

A FastAPI application that serves data from a **Databricks Lakebase (autoscaling Postgres)** synced table. It features OAuth-based connectivity with automatic token rotation, connection pooling tuned for scale-to-zero, and fully bundle-driven provisioning.

Learn more about Databricks Lakebase [here](https://docs.databricks.com/aws/en/oltp/).

## ❓ Why do you need an API?
- **Database Abstraction & Security**: APIs prevent direct database access and provide controlled access through authenticated apps.
- **Standardized Access Patterns**: APIs create consistent ways to interact with data across teams and applications.
- **Development Velocity**: Write your API logic once and let applications leverage your endpoint.
- **Performance Optimization & Caching**: Connection pooling, query optimization, and results caching for high-performance workloads.
- **Cross Platform Capability**: Any language can use the REST protocol.
- **Audit Trails & Monitoring**: Custom logging, request tracking, and usage analytics.
- **Future Proof**: APIs simplify switching databases, adding data sources, or changing infrastructure.

## 🌟 Features
- **FastAPI REST API** with async/await
- **Lakebase autoscaling Postgres** over OAuth (direct endpoint), with **psycopg3 (async)**
- **Automatic OAuth token rotation** — proactive refresh with retry/backoff and a connect-time staleness guard
- **Scale-to-zero-aware connection pool** — pre-ping, recycle below token lifetime, LIFO reuse
- **Bundle-driven provisioning** — one `databricks bundle deploy` creates the project, branch, endpoint, catalog, and synced table, deploys the app, and grants the app's service principal least-privilege (`USAGE` + `SELECT`) access via a post-deploy hook
- **Static API surface** — data endpoints are always registered and return `503` until the database is initialized (no restart needed)
- **Immediate example** over Databricks sample data (`samples.tpch.orders` → Postgres `public.orders_synced`)

## 📋 Prerequisites
- **Databricks Workspace** with permission to create **Lakebase projects** and **Apps**
- **Databricks CLI** (v1.1+) with an authenticated profile (`databricks auth login`) — used for bundle deploys
- **Python 3.11+** and the [uv package manager](https://docs.astral.sh/uv/getting-started/) (for local development)

## 🚀 Quick Start

### Deploy as a Databricks App (recommended)
Provisioning is owned by the bundle — there are no runtime "create resource" endpoints, and you don't edit `app.yaml` or `.env` to deploy.

1. **Clone & authenticate:**
   ```bash
   git clone https://github.com/databricks-solutions/lakebase-fastapi-app.git
   cd lakebase-fastapi-app
   databricks auth login   # profile must be able to create Lakebase projects + apps
   ```
2. **Set the three bundle variables** for your workspace (edit the `variables:` defaults in `databricks.yml`, or pass `--var` at deploy):

   | Variable | What to set it to |
   |----------|-------------------|
   | `project_name` | A Lakebase project id, unique per deployment (the single place the name lives) |
   | `pipeline_storage_catalog` | A UC catalog **you can write to** (holds the sync pipeline's checkpoints) |
   | `pipeline_storage_schema` | A schema in that catalog |

3. **Deploy:**
   ```bash
   databricks bundle deploy -t dev \
     --var="project_name=my-lakebase-app" \
     --var="pipeline_storage_catalog=my_catalog" \
     --var="pipeline_storage_schema=my_schema"
   ```
   This creates the Lakebase project/branch/endpoint/catalog/synced table, deploys the app, and the `postdeploy` hook ([`scripts/grant_app_access.py`](scripts/grant_app_access.py)) grants the app's service principal least-privilege `USAGE` + `SELECT` on the synced table — run as you (the project creator/superuser), so **no manual permission step is required**.
4. **Open the app** (`<your_app_url>/docs`). The `/api/v1` endpoints serve the synced data. (They return **`503`** until the first sync provisions the table.)
5. **Tear down** when finished:
   ```bash
   databricks bundle destroy -t dev
   ```

> **Why so little config?** The app's database resource binding auto-injects the connection (`PGHOST`/`PGPORT`/`PGDATABASE`/`PGUSER`/`PGSSLMODE`), and `ENDPOINT_NAME` is injected from the bundle (`config.env`). So **no project/db names live in `app.yaml`** — change the name with one `--var project_name=...`.

### Local development
1. **Install dependencies:**
   ```bash
   uv sync
   ```
2. **Configure the one connection identifier:**
   ```bash
   cp .env.example .env
   ```
   Set `ENDPOINT_NAME` to your deployed project's endpoint (the app derives host/user/database from it):
   ```
   ENDPOINT_NAME=projects/<your-project>/branches/production/endpoints/primary
   ```
3. **Run the application:**
   ```bash
   uv run uvicorn src.app:app --reload
   ```
4. **Access the API:**
   - API: `http://localhost:8000`
   - Interactive docs: `http://localhost:8000/docs`

> Locally you connect as **your own identity** (a project superuser) using your CLI profile, so the order endpoints work against the synced table as soon as it exists. Data endpoints return **`503`** until the engine connects — there's no restart step or conditional endpoint registration.

## ⚙️ Configuration

### Deployment — bundle variables (`databricks.yml`)
The single source of truth for a deployment. Override with `--var` or edit the defaults:

| Variable | Description | Default |
|----------|-------------|---------|
| `project_name` | Lakebase project id (and display name) — **the one place** to change the name | `lakebase-fastapi-app-db` |
| `catalog_name` | Postgres-backed UC catalog the bundle creates (unique per project) | `lakebase-fastapi-app-catalog` |
| `pg_database` | Postgres database name | `databricks_postgres` |
| `source_table` | UC source table to sync | `samples.tpch.orders` |
| `pipeline_storage_catalog` | Writable catalog for sync-pipeline checkpoints | `your_storage_catalog` |
| `pipeline_storage_schema` | Writable schema for sync-pipeline checkpoints | `your_storage_schema` |

> When deployed, the app's DB binding auto-injects `PGHOST`/`PGPORT`/`PGDATABASE`/`PGUSER`/`PGSSLMODE`, and `ENDPOINT_NAME` is injected via the app's `config.env` (derived from the endpoint resource). `PGUSER` = the app's `DATABRICKS_CLIENT_ID` (its Postgres role). **No project/db names are set in `app.yaml`.**

### Local development (`.env`)
Local runs don't get the injected `PG*`, so set the one endpoint identifier (plus the synced-table location used by the grant script):

| Variable | Description | Example |
|----------|-------------|---------|
| `ENDPOINT_NAME` | Lakebase endpoint resource path — the app derives host/user/database from it | `projects/<p>/branches/production/endpoints/primary` |
| `DEFAULT_POSTGRES_SCHEMA` | Synced-table schema (also read by the post-deploy grant script) | `public` |
| `DEFAULT_POSTGRES_TABLE` | Synced-table name (also read by the post-deploy grant script) | `orders_synced` |

### Optional connection-pool tuning (`app.yaml` env, or `.env` locally)

| Variable | Default | Description |
|----------|---------|-------------|
| `DB_POOL_SIZE` | `5` | Connection pool size |
| `DB_MAX_OVERFLOW` | `10` | Max overflow connections |
| `DB_POOL_TIMEOUT` | `30` | Pool checkout timeout (seconds) |
| `DB_COMMAND_TIMEOUT` | `10` | Per-statement server-side timeout (seconds) |
| `DB_POOL_RECYCLE_INTERVAL` | `2700` | Recycle connections (seconds) — below the 60-min token lifetime |

## 🏗️ Architecture

### Project structure
```
src/
├── app.py                  # FastAPI app + lifespan (engine init, token refresh, health)
├── core/
│   └── database.py         # psycopg3 async engine, OAuth token rotation, require_db guard
├── models/
│   └── orders.py           # Orders models (SQLModel)
└── routers/
    ├── __init__.py         # Static router registration
    └── v1/
        ├── healthcheck.py  # Health endpoint
        └── orders.py       # Order endpoints (guarded by require_db -> 503 until ready)
scripts/
└── grant_app_access.py     # Post-deploy: grant the app SP USAGE+SELECT (least privilege)
databricks.yml              # Bundle: all Lakebase resources + the app + postdeploy hook
```

### Database connection strategy
The app connects to the autoscaling **endpoint host** using an OAuth credential as the Postgres password (driver: `postgresql+psycopg`, psycopg3 async).

**Token lifecycle** (Lakebase OAuth credentials last ~60 minutes):
- A background task refreshes **proactively at ~45 minutes** with retry/backoff.
- A SQLAlchemy `do_connect` hook injects the current token and, as a safety net, refreshes synchronously if the token is near expiry.
- The pool recycles below the token lifetime (`DB_POOL_RECYCLE_INTERVAL=2700`).

**Pool resilience for scale-to-zero:** the endpoint suspends when idle, killing pooled connections. `pool_pre_ping=True` validates each connection on checkout, `pool_use_lifo=True` reuses hot connections, and recycling ages out the rest.

### Provisioning & permissions
Everything is created by `databricks bundle deploy`:
- `postgres_projects` / `postgres_branches` / `postgres_endpoints` — the autoscaling database.
- `postgres_catalogs` — a Postgres-backed UC catalog (mirrors the DB, so it has a `public` schema).
- `postgres_synced_tables` — `samples.tpch.orders` → Postgres `public.orders_synced` (SNAPSHOT).
- `apps` — the FastAPI app, bound to the database (`CAN_CONNECT_AND_CREATE`).
- `experimental.scripts.postdeploy` — grants the app SP **least-privilege** `USAGE` + `SELECT`, run as the deployer. The app SP does **not** need `CAN MANAGE` or `databricks_superuser`.

## 📚 API Documentation

### Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Liveness check (no DB dependency) |
| `/api/v1/healthcheck` | GET | Service health |
| `/api/v1/count` | GET | Total order count |
| `/api/v1/sample` | GET | 5 random order keys |
| `/api/v1/pages` | GET | Page-based pagination |
| `/api/v1/stream` | GET | Cursor-based pagination (high performance) |
| `/api/v1/{order_key}` | GET | Get an order by key |

> Data endpoints return **`503`** until the database engine is initialized.
>
> This API is **read-only** over the synced table (which is read-only and SNAPSHOT-overwritten, and the app SP holds `SELECT`-only). To add writes, create an **app-owned** table and grant the SP write access on it — don't write to the synced table.

### Example requests

```bash
# Liveness
curl http://localhost:8000/health

# Total order count
curl http://localhost:8000/api/v1/count

# A specific order
curl http://localhost:8000/api/v1/1

# Page-based pagination
curl "http://localhost:8000/api/v1/pages?page=1&page_size=10"

# Cursor-based pagination
curl "http://localhost:8000/api/v1/stream?cursor=0&page_size=10"
```

### Response format

```json
{
  "o_orderkey": 1,
  "o_custkey": 36901,
  "o_orderstatus": "F",
  "o_totalprice": 172799.49,
  "o_orderdate": "1996-01-02",
  "o_orderpriority": "5-LOW",
  "o_clerk": "Clerk#000000951",
  "o_shippriority": 0,
  "o_comment": "nstructions sleep furiously among"
}
```

## 🔗 Connecting Apps

View the [apps cookbook](https://apps-cookbook.dev/docs/fastapi/getting_started/connections/) to learn how to:
- **Connect a local machine to Apps**
- **Connect an external app to a Databricks App**
- **Connect a Databricks App to a Databricks App**

## 🔧 Performance Tuning

For high-traffic applications:
1. **Increase pool size** (mind the endpoint's `max_connections`, which scales with CU, and that each uvicorn worker owns its own pool):
   ```env
   DB_POOL_SIZE=10
   DB_MAX_OVERFLOW=10
   ```
2. **Prefer cursor pagination** (`/api/v1/stream`) for large scans — it is O(page), not O(offset).
3. **Monitor pool utilization** in application logs.

## 🛡️ Security
- **OAuth token rotation** prevents credential staleness; tokens are never logged.
- **Least-privilege grants** — the app SP gets only `USAGE` + `SELECT` on the synced table.
- **SSL/TLS enforced** (`sslmode=require`) for all database connections.
- **Environment variable isolation** for sensitive configuration.

## 📊 Monitoring

### Key metrics
- **Request latency** (`X-Process-Time` header)
- **Token refresh events** (logs)
- **Connection pool utilization**
- **Database query performance**

### Log messages
```
Token refresh succeeded (attempt 1)
Request: GET /api/v1/1 - 8.3ms
```

## 🚨 Troubleshooting

- **`503` from data endpoints:** the engine hasn't initialized — confirm the Lakebase project exists and the app can reach it (and that the `postdeploy` grant ran).
- **`permission denied for table`:** the app SP lacks `SELECT` — re-run `databricks bundle deploy` (which re-runs the grant) or apply the grant manually.
- **Slow queries:** raise `DB_COMMAND_TIMEOUT` (now enforced as a server-side `statement_timeout`).

## How to get help

Databricks support doesn't cover this content. For questions or bugs, please open a GitHub issue and the team will help on a best-effort basis.

## 📄 License

&copy; 2025 Databricks, Inc. All rights reserved. The source is provided subject to the [Databricks License](https://databricks.com/db-license-source).

| Library | Description | License | Source |
|---------|-------------|---------|---------|
| FastAPI | High-performance API framework | MIT | [GitHub](https://github.com/tiangolo/fastapi) |
| SQLAlchemy / SQLModel | SQL toolkit, ORM, and typed models | MIT | [GitHub](https://github.com/sqlalchemy/sqlalchemy) |
| Databricks SDK | Official Databricks SDK | Apache 2.0 | [GitHub](https://github.com/databricks/databricks-sdk-py) |
| psycopg | PostgreSQL driver (psycopg3) | LGPL | [GitHub](https://github.com/psycopg/psycopg) |
| Pydantic | Data validation using Python type hints | MIT | [GitHub](https://github.com/pydantic/pydantic) |

| Dataset | Disclaimer |
|---------|-------------|
| TPC-H | The TPC-H Dataset is available without charge from TPC under the terms of the [TPC End User License Agreement](https://tpc.org/TPC_Documents_Current_Versions/txt/eula.txt). |
