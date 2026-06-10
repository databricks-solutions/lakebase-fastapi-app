"""Post-deploy hook: grant the app's service principal LEAST-PRIVILEGE read
access to the synced table.

Runs locally as the *deployer* (the project creator, a Lakebase superuser), so
no CAN MANAGE pre-grant on the app SP is needed. Grants only USAGE on the schema
and SELECT on the synced table — not DATABRICKS_SUPERUSER (per least-privilege).

Invoked by `experimental.scripts.postdeploy` in databricks.yml, after the app
(and thus its service principal + Postgres binding) exists. The project/branch
are read from the app's Postgres binding — NOT from .env — so the grant always
targets the project this bundle just deployed, even if your local .env drifts.
"""

import os
import sys
import time

from databricks.sdk import WorkspaceClient
from dotenv import load_dotenv
import psycopg
from psycopg import sql

load_dotenv()

APP_NAME = os.getenv("APP_NAME", "lakebase-fastapi-app")
ENDPOINT = os.getenv("LAKEBASE_ENDPOINT", "primary")
DB = os.getenv("LAKEBASE_DATABASE_NAME", "databricks_postgres")
SCHEMA = os.getenv("DEFAULT_POSTGRES_SCHEMA", "public")
TABLE = os.getenv("DEFAULT_POSTGRES_TABLE", "orders_synced")

# How long to wait for the synced table to materialize before granting SELECT.
TABLE_WAIT_SECONDS = 90
TABLE_POLL_INTERVAL = 3


def main() -> int:
    w = WorkspaceClient()

    # 1. Resolve the app: its service principal (Postgres role name = this UUID)
    #    and its Postgres binding (gives the branch this bundle deployed).
    app = w.apps.get(name=APP_NAME)
    sp = app.service_principal_client_id
    if not sp:
        print(f"[grant] Could not resolve service principal for app '{APP_NAME}'.")
        return 1

    branch_name = next(
        (
            r.postgres.branch
            for r in (app.resources or [])
            if getattr(r, "postgres", None) and r.postgres.branch
        ),
        None,
    )
    if not branch_name:
        print(f"[grant] App '{APP_NAME}' has no Postgres resource binding.")
        return 1
    print(f"[grant] App '{APP_NAME}' SP: {sp}")
    print(f"[grant] Branch (from app binding): {branch_name}")

    # 2. Connect as the DEPLOYER (a superuser) — the synced table is owned by the
    #    creating superuser, so only a superuser can grant SELECT on it.
    endpoint_name = f"{branch_name}/endpoints/{ENDPOINT}"
    host = w.postgres.get_endpoint(name=endpoint_name).status.hosts.host
    token = w.postgres.generate_database_credential(endpoint=endpoint_name).token
    deployer = w.current_user.me().user_name

    conninfo = f"host={host} port=5432 dbname={DB} user={deployer} sslmode=require"
    with psycopg.connect(
        conninfo, password=token, autocommit=True, prepare_threshold=None
    ) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS databricks_auth")
        try:
            # Ensure the SP has a Postgres role (idempotent; usually already
            # created by the app's Lakebase resource binding).
            conn.execute("SELECT databricks_create_role(%s, 'SERVICE_PRINCIPAL')", (sp,))
        except Exception as e:  # noqa: BLE001 - role likely already exists
            print(f"[grant] databricks_create_role skipped: {e}")

        # USAGE on the schema (the schema exists immediately).
        conn.execute(
            sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                sql.Identifier(SCHEMA), sql.Identifier(sp)
            )
        )
        print(f"[grant] Granted USAGE on {SCHEMA} to {sp}.")

        # The synced table is provisioned asynchronously; wait for it before SELECT.
        fqtn = f"{SCHEMA}.{TABLE}"
        deadline = time.monotonic() + TABLE_WAIT_SECONDS
        while conn.execute("SELECT to_regclass(%s)", (fqtn,)).fetchone()[0] is None:
            if time.monotonic() >= deadline:
                print(
                    f"[grant] WARNING: {fqtn} not present after {TABLE_WAIT_SECONDS}s. "
                    "USAGE granted; re-run the deploy once the sync provisions the "
                    "table to grant SELECT."
                )
                return 0
            print(f"[grant] Waiting for {fqtn} to be created...")
            time.sleep(TABLE_POLL_INTERVAL)

        conn.execute(
            sql.SQL("GRANT SELECT ON {}.{} TO {}").format(
                sql.Identifier(SCHEMA), sql.Identifier(TABLE), sql.Identifier(sp)
            )
        )
        print(f"[grant] Granted SELECT on {fqtn} to {sp}.")

    print("[grant] Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
