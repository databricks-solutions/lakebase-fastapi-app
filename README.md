![Databricks Apps](https://img.shields.io/badge/Databricks-Apps-orange)
[![Python](https://img.shields.io/badge/Python-3.13-3776AB.svg?style=flat&logo=python&logoColor=white)](https://www.python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115.0-009688.svg?style=flat&logo=FastAPI&logoColor=white)](https://fastapi.tiangolo.com)
![GitHub stars](https://img.shields.io/github/stars/databricks-solutions/lakebase-fastapi-app?style=social)
![GitHub forks](https://img.shields.io/github/forks/databricks-solutions/lakebase-fastapi-app?style=social)
![GitHub issues](https://img.shields.io/github/issues/databricks-solutions/lakebase-fastapi-app)
![GitHub last commit](https://img.shields.io/github/last-commit/databricks-solutions/lakebase-fastapi-app)

# 🌊 Lakebase FastAPI Databricks App.

A production-ready FastAPI application for accessing Databricks Lakebase data. Features scalable architecture, automatic token refresh, and optimized database connection management.

Learn more about Databricks Lakebase [here](https://docs.databricks.com/aws/en/oltp/)

## ❓ Why do you need an api? 
- **Database Abstraction & Security**:  APIs prevent direct database access and provide controlled access through authenticated apps. 
- **Standardized Access Patterns**: APIs create consistent ways to interact with data across different teams and applications. 
- **Development Velocity**:   APIs reduce duplicate code in applications. Write your api logic once and let applications leverage your endpoint.
- **Performance Optimization & Caching**:  APIs leverage connection pooling, query optimization, and results caching for high performance workloads.
- **Cross Platform Capability**: Any programming language can leverage the REST protocol. 
- **Audit Trails & Monitoring**: Custom logging, request tracking, and usage analytics give visibility into data access.
- **Future Proof**:  APIs simplify switching between databases, adding new data sources, or changing infrastructure.

## 🌟 Features
- **FastAPI REST API** with async/await support
- **Databricks Lakebase Integration** with OAuth token management
- **Automatic Resource Management** - create and delete Lakebase resources on-demand
- **Dynamic Endpoint Registration** - endpoints are conditionally loaded based on database availability
- **Automatic Token Refresh** with configurable intervals
- **Production-Ready Architecture** with domain-driven design
- **Connection Pooling** with optimized settings for high-traffic scenarios
- **Environment-Based Configuration** for different deployment environments
- **Comprehensive Error Handling** and logging
- **Immediate Example** plugs into databricks sample datasets

## 📋 Prerequisites
- **Databricks Workspace**: Permissions to create apps and database instances
- **Python 3.11+** and [uv package manager](https://docs.astral.sh/uv/getting-started/)
- **Environment Variables** configured (see Configuration section)

## 🚀 Quick Start


### Local Development

1. **Clone and install dependencies:**
   ```bash
   git clone https://github.com/databricks-solutions/lakebase-fastapi-app.git
   uv sync
   ```

2. **Configure environment variables:**
   ```bash
   cp .env.example .env
   # Edit .env with your Databricks configuration
   ```

3. **Run the application:**
   ```bash
   uv run uvicorn src.main:app --reload
   ```

4. **Access the API:**
   - API: `http://localhost:8000`
   - Interactive docs: `http://localhost:8000/docs`

5. **Create Lakebase Resources for Orders endpoints:**
   - Docs page: `http://localhost:8000/docs`
   - Click Dropdown: `/api/v1/resources/create-lakebase-resources`
   - Click 'Try it Out'
   - Set create_resources = true 
   - Click Execute (**Resources will take several minutes to deploy**)

6. **Orders Endpoints:**
   - Confirm resources from step 5 are deployed.
   - Restart the fastapi service
   - You should now see the /orders endpoints.

7. **Delete Lakebase Resources:**
   - Docs page: `http://localhost:8000/docs`
   - Click Dropdown: `/api/v1/resources/delete-lakebase-resources`
   - Click 'Try it Out'
   - Set confirm_deletion = true 
   - Click Execute

### Databricks Apps Deployment
   *assumes local development steps have been completed.
1. **Databricks UI: Create Custom App:**

2. **Databricks UI: App Database Instance Permissions:**
   - Copy App Service Principal Id from App -> Authorization
   - Compute -> Database Instances -> <your_instance> -> Permissions
   - Add PostgreSQL Role -> enter app service principal id -> assign databricks superuser
   - Grant App Service Principal permissions to the Postgres Catalog.

3. **Configure environment variables in app.yaml:**

   #### ⚙️ Configuration

   ### Required Environment Variables

   | Variable | Description | Example |
   |----------|-------------|---------|
   | `LAKEBASE_INSTANCE_NAME` | Lakebase database instance name | `my-lakebase-instance` |
   | `LAKEBASE_DATABASE_NAME` | Lakebase database name | `demo-database` |
   | `LAKEBASE_CATALOG_NAME` | Lakebase catalog name | `my-lakebase-catalog` |
   | `SYNCHED_TABLE_STORAGE_CATALOG` | Catalog for synced table metadata | `my_catalog` |
   | `SYNCHED_TABLE_STORAGE_SCHEMA` | Schema for synced table metadata | `my_schema` |
   | `DATABRICKS_DATABASE_PORT` | Postgres Port | `5432` |
   | `DEFAULT_POSTGRES_SCHEMA` | Database schema | `public` |
   | `DEFAULT_POSTGRES_TABLE` | Table name | `orders_synced` |

   ### Optional Configuration

   | Variable | Default | Description |
   |----------|---------|-------------|
   | `DB_POOL_SIZE` | `5` | Connection pool size |
   | `DB_MAX_OVERFLOW` | `10` | Max overflow connections |
   | `DB_POOL_TIMEOUT` | `30` | Pool checkout timeout (seconds) |
   | `DB_COMMAND_TIMEOUT` | `10` | Query timeout (seconds) |
   | `DB_POOL_RECYCLE_INTERVAL` | `3600` | Pool Recycle Interval (seconds) |

4. **Deploy app files using Databricks CLI:**
   ```bash
   databricks sync --watch . /Workspace/Users/<your_username>/<project_folder> # May need -p <profile_name> depending on .databrickscfg 
   ```
5. **Databricks UI: Deploy Application:**
   - App -> Deploy
   - Source code path = /Workspace/Users/<your_username>/<project_folder> - source code path is at the project root where app.yaml lives. 
   - View logs for successful deploy: src.main - INFO - Application startup initiated
   - View your API docs: <your_app_url>/docs

## 🏗️ Architecture

### Project Structure
```
src/
├── app.py                   # Main FastAPI application with dynamic endpoint loading
├── core/
│   └── database.py         # Database connection with automatic token refresh
├── models/
│   ├── lakebase.py         # Lakebase resource management models
│   └── orders.py           # Orders models using SQLModel
└── routers/
    ├── __init__.py         # Dynamic router registration logic
    └── v1/                 # API v1 endpoints
        ├── healthcheck.py  # Health check endpoints
        ├── lakebase.py     # Lakebase resource management endpoints
        └── orders.py       # Orders endpoints (loaded dynamically)
```

### Database Connection Strategy
**Important Note:** OAuth tokens expire after one hour, but expiration is enforced only at login. Open connections remain active even if the token expires. However, any PostgreSQL command that requires authentication fails if the token has expired.  Read More: https://docs.databricks.com/aws/en/oltp/oauth

**Automatic Token Refresh:**
- 50 Minute token refresh with background async task that does not impact requests
- Guaranteed token refresh before expiry (safe for 1-hour token lifespans)
- Optimized for high-traffic production applications
- Pool connections are recycled every hour preventing expired tokens on long connections

### Authentication Modes

The application supports two authentication modes for database connections:

**App-Level Authentication (Default)**
- Configuration: `USER_BASED_AUTHENTICATION=false` or omitted from .env
- Single shared connection pool for all users
- Uses service principal or app-level credentials
- Best for: Development, single-user apps, or environments without user-specific access control

**User-Based Authentication**
- Configuration: `USER_BASED_AUTHENTICATION=true` in .env
- Per-user database connections using Databricks Apps forwarded headers
- Each request authenticated as the specific user via X-Forwarded-Access-Token
- Enables Unity Catalog row-level and column-level security
- User credentials cached for 45 minutes to minimize API calls
- Best for: Multi-tenant production apps requiring user-specific access control

When user-based authentication is enabled, the app extracts user information from these Databricks Apps headers:
- `X-Forwarded-Access-Token`: User's OAuth token
- `X-Forwarded-Email`: User's email address
- `X-Forwarded-User`: User identifier

For more details, see [Databricks Apps HTTP Headers](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/http-headers)

## 📚 API Documentation

### Core Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Simple health check |
| `/api/v1/health/database` | GET | Database health check |
| `/api/v1/resources/create-lakebase-resources` | POST | Create Lakebase resources |
| `/api/v1/resources/delete-lakebase-resources` | DELETE | Delete Lakebase resources |

### Dynamic Order Endpoints
*These endpoints are only available when a Lakebase database instance exists*

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/orders/count` | GET | Get total order count |
| `/api/v1/orders/sample` | GET | Get 5 random order keys |
| `/api/v1/orders/{order_key}` | GET | Get order by key |
| `/api/v1/orders/pages` | GET | Page-based pagination (traditional) |
| `/api/v1/orders/stream` | GET | Cursor-based pagination (high performance) |
| `/api/v1/orders/{order_key}/status` | POST | Update order status |

### Example Requests

```bash
# Check if app is running
curl http://localhost:8000/health

# Check database health
curl http://localhost:8000/api/v1/health/database

# Create Lakebase resources
curl -X POST http://localhost:8000/api/v1/resources/create-lakebase-resources \
  -H "Content-Type: application/json" \
  -d '{}'

# Get order count (only available after resources are created)
curl http://localhost:8000/api/v1/orders/count

# Get specific order
curl http://localhost:8000/api/v1/orders/1

# Get paginated orders
curl "http://localhost:8000/api/v1/orders/pages?page=1&page_size=10"

# Get cursor-based orders
curl "http://localhost:8000/api/v1/orders/stream?cursor=0&page_size=10"

# Update order status
curl -X POST http://localhost:8000/api/v1/orders/1/status \
  -H "Content-Type: application/json" \
  -d '{"o_orderstatus": "F"}'
```

### Response Format

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

### View [app-cookbook](https://apps-cookbook.dev/docs/fastapi/getting_started/connections/) to learn how to: 

- **Connect Local Machine to Apps**
- **Connect External App to Databricks App**
- **Connect Databricks App to Databricks App**

## 🔧 Performance Tuning

### High-Traffic Applications

For applications handling thousands of requests per minute:

1. **Increase pool size:**
   ```env
   DB_POOL_SIZE=20
   DB_MAX_OVERFLOW=50
   ```

2. **Monitor connection pool metrics** in application logs

## 🛡️ Security

- **OAuth token rotation** prevents credential staleness
- **SSL/TLS enforcement** for all database connections
- **Environment variable isolation** for sensitive configuration
- **No credential logging** in production builds

## 📊 Monitoring

### Key Metrics to Monitor

- **Request latency** (`X-Process-Time` header)
- **Token refresh frequency** (log analysis)
- **Connection pool utilization**
- **Database query performance**

### Log Messages

```
# Token refresh events
"Background token refresh: Generating fresh PostgreSQL OAuth token"
"Background token refresh: Token updated successfully"

# Performance tracking
"Request: GET /orders/1 - 8.3ms"
```

## 🚨 Troubleshooting

### Common Issues

**Connection timeouts:**
- Increase `DB_COMMAND_TIMEOUT` for slow queries
- Check database instance performance

## How to get help

Databricks support doesn't cover this content. For questions or bugs, please open a GitHub issue and the team will help on a best effort basis.

## 📄 License

&copy; 2025 Databricks, Inc. All rights reserved. The source in this notebook is provided subject to the [Databricks License](https://databricks.com/db-license-source).

| Library | Description | License | Source |
|---------|-------------|---------|---------|
| FastAPI | High-performance API framework | MIT | [GitHub](https://github.com/tiangolo/fastapi) |
| SQLAlchemy | SQL toolkit and ORM | MIT | [GitHub](https://github.com/sqlalchemy/sqlalchemy) |
| Databricks SDK | Official Databricks SDK | Apache 2.0 | [GitHub](https://github.com/databricks/databricks-sdk-py) |
| asyncpg | Async PostgreSQL driver | Apache 2.0 | [GitHub](https://github.com/MagicStack/asyncpg) |
| Pydantic | Data validation using Python type hints | MIT | [GitHub](https://github.com/pydantic/pydantic) |

| Dataset | Disclaimer |
|---------|-------------|
| TPC-H | The TPC-H Dataset is available without charge from TPC under the terms of the the [TPC End User License Agreement](https://tpc.org/TPC_Documents_Current_Versions/txt/eula.txt).