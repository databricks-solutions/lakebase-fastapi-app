import logging
import os

from google.protobuf.duration_pb2 import Duration
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.postgres import (
    Branch,
    BranchSpec,
    Endpoint,
    EndpointSpec,
    EndpointType,
    Project,
    ProjectDefaultEndpointSettings,
    ProjectSpec,
)
import requests
from ...models.lakebase import LakebaseResourcesDeleteResponse, LakebaseResourcesResponse

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger(__name__)
w = WorkspaceClient()
router = APIRouter(tags=["lakebase"])
current_user_id = w.current_user.me().id


@router.post(
    "/resources/create-lakebase-resources",
    response_model=LakebaseResourcesResponse,
    summary="Create Lakebase Resources",
)
async def create_lakebase_resources(
    create_resources: bool = Query(
        description="""🚨 This endpoint creates resources in your Databricks environment that will incur a cost.
        By setting this value to true you understand the costs associated with this action. 🚨
        ⌛️ This endpoint may take a few minutes to complete.⌛️""",
    ),
    autoscaling_min_cu: float = Query(
        0.5, description="Minimum autoscaling compute units (min 0.5)"
    ),
    autoscaling_max_cu: float = Query(
        4, description="Maximum autoscaling compute units"
    ),
    suspend_timeout_seconds: int = Query(
        300, description="Seconds of inactivity before auto-suspend (60-604800)"
    ),
):
    if not create_resources:
        logger.info("create_resources is set to False. No resources were created.")
        return LakebaseResourcesResponse(
            instance="",
            catalog="",
            synced_table="",
            message="No resources were created (create_resources=False)",
        )

    username_prefix = w.current_user.me().user_name.split("@")[0].replace(".", "-").lower()
    project_id = os.getenv(
        "LAKEBASE_PROJECT_ID", f"{username_prefix}-lakebase-demo"
    )
    branch_id = os.getenv("LAKEBASE_BRANCH", "main")
    endpoint_id = "primary"
    project_name = f"projects/{project_id}"
    branch_name = f"{project_name}/branches/{branch_id}"

    # Check if project already exists (idempotent: continue to ensure all resources)
    try:
        w.postgres.get_project(name=project_name)
        logger.info(f"Project {project_id} already exists. Ensuring remaining resources.")
    except Exception as e:
        if "not found" in str(e).lower():
            logger.info(f"Project {project_id} does not exist. Proceeding with creation.")
        else:
            logger.error(f"Error checking project existence: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Error checking project existence: {str(e)}",
            )

    lakebase_database_name = os.getenv("LAKEBASE_DATABASE_NAME", "databricks_postgres")
    catalog_name = os.getenv(
        "LAKEBASE_CATALOG_NAME", f"{username_prefix}_pg_catalog".replace("-", "_")
    )
    synced_table_storage_catalog = os.getenv(
        "SYNCED_TABLE_STORAGE_CATALOG", "default_storage_catalog"
    )
    synced_table_storage_schema = os.getenv(
        "SYNCED_TABLE_STORAGE_SCHEMA", "default_storage_schema"
    )
    # Synced table DESTINATION postgres schema + table (also what the app queries).
    # The schema segment determines the Postgres landing schema (e.g. public).
    destination_schema = os.getenv("DEFAULT_POSTGRES_SCHEMA", "public")
    destination_table = os.getenv("DEFAULT_POSTGRES_TABLE", "orders_synced")

    # 1. Create autoscaling postgres project
    project = Project(
        spec=ProjectSpec(
            display_name=project_id,
            pg_version=17,
            default_endpoint_settings=ProjectDefaultEndpointSettings(
                autoscaling_limit_min_cu=autoscaling_min_cu,
                autoscaling_limit_max_cu=autoscaling_max_cu,
                suspend_timeout_duration=Duration(seconds=suspend_timeout_seconds),
            ),
        )
    )
    logger.info(f"Creating autoscaling postgres project: {project_id}")
    try:
        project_result = w.postgres.create_project(
            project=project, project_id=project_id
        ).wait()
        logger.info(f"Project created: {project_result.name}")
    except Exception as e:
        if "already exists" in str(e).lower():
            logger.info(f"Project {project_id} already exists, continuing.")
        else:
            raise

    # 2. Create branch
    branch = Branch(
        spec=BranchSpec(
            is_protected=False,
            no_expiry=True,
        )
    )
    logger.info(f"Creating branch: {branch_id}")
    try:
        branch_result = w.postgres.create_branch(
            parent=project_name, branch=branch, branch_id=branch_id
        ).wait()
        logger.info(f"Branch created: {branch_result.name}")
    except Exception as e:
        if "already exists" in str(e).lower():
            logger.info(f"Branch {branch_id} already exists, continuing.")
        else:
            raise

    # 3. Create read-write endpoint
    endpoint = Endpoint(
        spec=EndpointSpec(
            endpoint_type=EndpointType.ENDPOINT_TYPE_READ_WRITE,
            autoscaling_limit_min_cu=autoscaling_min_cu,
            autoscaling_limit_max_cu=autoscaling_max_cu,
            suspend_timeout_duration=Duration(seconds=suspend_timeout_seconds),
        )
    )
    logger.info(f"Creating endpoint: {endpoint_id}")
    try:
        endpoint_result = w.postgres.create_endpoint(
            parent=branch_name, endpoint=endpoint, endpoint_id=endpoint_id
        ).wait()
        logger.info(f"Endpoint created: {endpoint_result.name}")
    except Exception as e:
        if "already exists" in str(e).lower():
            logger.info(f"Endpoint {endpoint_id} already exists, continuing.")
        else:
            raise

    # NOTE: No explicit superuser role is created. The identity that creates the
    # project/branch is automatically granted Postgres superuser on Lakebase.

    # 4. Create UC catalog via postgres catalogs API
    # (SDK doesn't have these methods yet, use REST directly)
    api_base = w.config.host.rstrip("/")

    def _api_headers():
        return {**w.config.authenticate(), "Content-Type": "application/json", "Accept": "application/json"}

    # catalog_id is a query param; the spec body carries the branch + postgres database
    catalog_payload = {
        "spec": {
            "branch": branch_name,
            "postgres_database": lakebase_database_name,
            "create_database_if_missing": True,
        }
    }
    logger.info(f"Creating catalog: {catalog_name}")
    catalog_resp = requests.post(
        f"{api_base}/api/2.0/postgres/catalogs",
        params={"catalog_id": catalog_name},
        json=catalog_payload,
        headers=_api_headers(),
    )
    if catalog_resp.status_code == 400 and "already exists" in catalog_resp.text.lower():
        logger.info(f"Catalog {catalog_name} already exists, continuing.")
        catalog_data = {"name": catalog_name}
    elif not catalog_resp.ok:
        logger.error(f"Catalog creation failed: {catalog_resp.status_code} {catalog_resp.text}")
        catalog_resp.raise_for_status()
    else:
        catalog_data = catalog_resp.json()
        logger.info(f"Created catalog: {catalog_data.get('name')}")

    # 5. Create synced table via postgres synced_tables API
    # synced_table_id is the DESTINATION UC table (catalog.schema.table) where the
    # synced table is registered — you must have USE_SCHEMA + CREATE TABLE there.
    # The spec body carries the Lakebase target (branch + postgres_database),
    # the source table to sync from, and the pipeline storage location.
    # Register the synced table in the Postgres-backed catalog (it mirrors the
    # Postgres DB, so its `public` schema maps to Postgres `public` — what the app
    # reads). storage_catalog/schema below is the pipeline's checkpoint storage only.
    synced_table_full_name = (
        f"{catalog_name}.{destination_schema}.{destination_table}"
    )
    synced_payload = {
        "spec": {
            "branch": branch_name,
            "postgres_database": lakebase_database_name,
            "source_table_full_name": "samples.tpch.orders",
            "primary_key_columns": ["o_orderkey"],
            "timeseries_key": "o_orderdate",
            "create_database_objects_if_missing": True,
            "new_pipeline_spec": {
                "storage_catalog": synced_table_storage_catalog,
                "storage_schema": synced_table_storage_schema,
            },
            "scheduling_policy": "SNAPSHOT",
        },
    }
    logger.info(f"Creating synced table: {synced_table_full_name}")
    sync_error = None
    pipeline_id = None
    try:
        sync_resp = requests.post(
            f"{api_base}/api/2.0/postgres/synced_tables",
            params={"synced_table_id": synced_table_full_name},
            json=synced_payload,
            headers=_api_headers(),
        )
        if not sync_resp.ok:
            sync_error = f"{sync_resp.status_code} {sync_resp.text}"
            logger.error(f"Synced table creation failed: {sync_error}")
        sync_resp.raise_for_status()
        sync_data = sync_resp.json()
        # pipeline_id lives under status; on create it may not be populated yet
        # while the pipeline provisions asynchronously.
        pipeline_id = sync_data.get("status", {}).get("pipeline_id")
        logger.info(
            f"Synced table created: {sync_data.get('name', synced_table_full_name)} "
            f"(pipeline_id={pipeline_id})"
        )
    except Exception as e:
        if sync_error is None:
            sync_error = str(e)
        logger.error(f"API error during synced table creation: {e}")

    workspace_url = w.config.host
    if sync_error is not None:
        message = (
            "Project/branch/endpoint/catalog created, but synced table creation "
            f"FAILED: {sync_error}"
        )
    elif pipeline_id:
        message = (
            f"Resources created successfully. Synced table {synced_table_full_name} "
            f"created; pipeline {pipeline_id} is provisioning asynchronously. "
            f"Monitor progress at: {workspace_url}/pipelines/{pipeline_id}"
        )
    else:
        message = (
            f"Resources created successfully. Synced table {synced_table_full_name} "
            f"created; pipeline is provisioning. Check {workspace_url}/pipelines"
        )

    return LakebaseResourcesResponse(
        instance=project_id,
        catalog=catalog_data.get("name", catalog_name),
        synced_table=pipeline_id or synced_table_full_name,
        message=message,
    )


@router.delete(
    "/resources/delete-lakebase-resources",
    response_model=LakebaseResourcesDeleteResponse,
    summary="Delete Lakebase Resources",
)
async def delete_lakebase_resources(
    confirm_deletion: bool = Query(
        description="""🚨 This endpoint will permanently delete Lakebase resources.
        Set to true to confirm you want to delete these resources. 🚨
        ⌛️ This endpoint may take a few minutes to complete.⌛️""",
    ),
):
    if not confirm_deletion:
        logger.info("confirm_deletion is set to False. No resources were deleted.")
        return LakebaseResourcesDeleteResponse(
            deleted_resources=[],
            failed_deletions=[],
            message="No resources were deleted (confirm_deletion=False)",
        )

    username_prefix = w.current_user.me().user_name.split("@")[0].replace(".", "-").lower()
    project_id = os.getenv(
        "LAKEBASE_PROJECT_ID", f"{username_prefix}-lakebase-demo"
    )
    catalog_name = os.getenv("LAKEBASE_CATALOG_NAME", f"{username_prefix}_pg_catalog".replace("-", "_"))
    synced_table_name = f"{catalog_name}.public.orders_synced"
    project_name = f"projects/{project_id}"

    deleted_resources = []
    failed_deletions = []

    api_base = w.config.host.rstrip("/")

    def _api_headers():
        return {**w.config.authenticate(), "Content-Type": "application/json", "Accept": "application/json"}

    # Delete synced table first (depends on catalog)
    logger.info(f"Attempting to delete synced table: {synced_table_name}")
    try:
        resp = requests.delete(
            f"{api_base}/api/2.0/postgres/synced_tables/{synced_table_name}",
            headers=_api_headers(),
        )
        resp.raise_for_status()
        deleted_resources.append(f"Synced table: {synced_table_name}")
        logger.info(f"Successfully deleted synced table: {synced_table_name}")
    except Exception as e:
        failed_deletions.append(f"Synced table: {synced_table_name} - {str(e)}")
        logger.error(f"Failed to delete synced table {synced_table_name}: {e}")

    # Delete catalog (depends on project)
    logger.info(f"Attempting to delete catalog: {catalog_name}")
    try:
        resp = requests.delete(
            f"{api_base}/api/2.0/postgres/catalogs/{catalog_name}",
            headers=_api_headers(),
        )
        resp.raise_for_status()
        deleted_resources.append(f"Catalog: {catalog_name}")
        logger.info(f"Successfully deleted catalog: {catalog_name}")
    except Exception as e:
        failed_deletions.append(f"Catalog: {catalog_name} - {str(e)}")
        logger.error(f"Failed to delete catalog {catalog_name}: {e}")

    # Delete the entire postgres project (cascades branches/endpoints)
    logger.info(f"Attempting to delete postgres project: {project_id}")
    try:
        delete_op = w.postgres.delete_project(name=project_name)
        delete_op.wait()
        deleted_resources.append(f"Postgres project: {project_id}")
        logger.info(f"Successfully deleted postgres project: {project_id}")
    except Exception as e:
        failed_deletions.append(f"Postgres project: {project_id} - {str(e)}")
        logger.error(f"Failed to delete postgres project {project_id}: {e}")

    if failed_deletions:
        message = f"Deletion completed with errors. {len(deleted_resources)} resources deleted, {len(failed_deletions)} failed."
    else:
        message = f"All {len(deleted_resources)} resources deleted successfully."

    return LakebaseResourcesDeleteResponse(
        deleted_resources=deleted_resources,
        failed_deletions=failed_deletions,
        message=message,
    )
