"""Post-deploy hook: grant the app's service principal LEAST-PRIVILEGE read
access to the synced table.

Runs locally as the *deployer* (the project creator, a Lakebase superuser), so
no CAN MANAGE pre-grant on the app SP is needed. Grants only USAGE on the schema
and SELECT on the synced table — not DATABRICKS_SUPERUSER (per least-privilege).

Invoked by `experimental.scripts.postdeploy` in databricks.yml, after the app
(and thus its service principal) exists. Reads config from the local .env.
"""

import os
import sys

from databricks.sdk import WorkspaceClient
from dotenv import load_dotenv
import psycopg
from psycopg import sql

load_dotenv()

APP_NAME = os.getenv("APP_NAME", "lakebase-fastapi-app")
PROJECT_ID = os.getenv("LAKEBASE_PROJECT_ID", "lakebase-fastapi-app-db")
BRANCH = os.getenv("LAKEBASE_BRANCH", "production")
ENDPOINT = os.getenv("LAKEBASE_ENDPOINT", "primary")
DB = os.getenv("LAKEBASE_DATABASE_NAME", "databricks_postgres")
SCHEMA = os.getenv("DEFAULT_POSTGRES_SCHEMA", "public")
TABLE = os.getenv("DEFAULT_POSTGRES_TABLE", "orders_synced")


def main() -> int:
    w = WorkspaceClient()

    # 1. The app's service principal (its Postgres role name is this UUID).
    sp = w.apps.get(name=APP_NAME).service_principal_client_id
    if not sp:
        print(f"[grant] Could not resolve service principal for app '{APP_NAME}'.")
        return 1
    print(f"[grant] App '{APP_NAME}' service principal: {sp}")

    # 2. Endpoint host + a credential for the DEPLOYER (a superuser), used to run
    #    the GRANTs. The synced table is owned by the creating superuser, so only
    #    a superuser can grant SELECT on it.
    endpoint_name = f"projects/{PROJECT_ID}/branches/{BRANCH}/endpoints/{ENDPOINT}"
    host = w.postgres.get_endpoint(name=endpoint_name).status.hosts.host
    token = w.postgres.generate_database_credential(endpoint=endpoint_name).token
    deployer = w.current_user.me().user_name

    conninfo = f"host={host} port=5432 dbname={DB} user={deployer} sslmode=require"
    print(f"[grant] Granting USAGE+SELECT on {SCHEMA}.{TABLE} to {sp} as {deployer}...")
    with psycopg.connect(
        conninfo, password=token, autocommit=True, prepare_threshold=None
    ) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS databricks_auth")
        try:
            # Ensure the SP has a Postgres role (idempotent; usually created by
            # the app's Lakebase resource binding already).
            conn.execute(
                "SELECT databricks_create_role(%s, 'SERVICE_PRINCIPAL')", (sp,)
            )
        except Exception as e:  # noqa: BLE001 - role likely already exists
            print(f"[grant] databricks_create_role skipped: {e}")
        conn.execute(
            sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                sql.Identifier(SCHEMA), sql.Identifier(sp)
            )
        )
        conn.execute(
            sql.SQL("GRANT SELECT ON {}.{} TO {}").format(
                sql.Identifier(SCHEMA), sql.Identifier(TABLE), sql.Identifier(sp)
            )
        )
    print("[grant] Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
