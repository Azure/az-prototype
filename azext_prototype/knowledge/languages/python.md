# Python Language Patterns for Azure Prototypes

Reference patterns for Python-based Azure prototype applications. Agents should use these patterns when generating Python application code.

## FastAPI Application Structure (Recommended)

```
app/
  __init__.py
  main.py              # Application entry point
  config.py            # Configuration loading
  dependencies.py      # DI / shared dependencies
  routers/
    __init__.py
    health.py          # Health check endpoints
    api.py             # Business logic endpoints
  services/
    __init__.py
    azure_clients.py   # Azure SDK client factories
  models/
    __init__.py
    schemas.py         # Pydantic request/response models
tests/
  __init__.py
  conftest.py          # Shared fixtures
  test_health.py
  test_api.py
Dockerfile
requirements.txt
.env.example
```

### main.py
```python
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_settings
from app.routers import health, api
from app.services.azure_clients import init_azure_clients, close_azure_clients

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    settings = get_settings()
    logger.info("Starting application: %s", settings.app_name)
    await init_azure_clients()
    yield
    await close_azure_clients()
    logger.info("Application shutdown complete")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        lifespan=lifespan,
    )
    app.include_router(health.router, tags=["health"])
    app.include_router(api.router, prefix="/api/v1", tags=["api"])
    return app


app = create_app()
```

### config.py
```python
from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    app_name: str = "prototype-api"
    app_version: str = "0.1.0"
    debug: bool = False

    # Azure
    azure_client_id: str = ""
    azure_storage_endpoint: str = ""
    azure_keyvault_endpoint: str = ""
    azure_cosmos_endpoint: str = ""
    azure_openai_endpoint: str = ""
    azure_openai_deployment: str = ""

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"

    class Config:
        env_file = ".env"
        case_sensitive = False


@lru_cache()
def get_settings() -> Settings:
    return Settings()
```

### dependencies.py
```python
from functools import lru_cache

from azure.identity import DefaultAzureCredential, ManagedIdentityCredential

from app.config import get_settings


@lru_cache()
def get_credential():
    """Get Azure credential (Managed Identity in production, CLI locally)."""
    settings = get_settings()
    if settings.azure_client_id:
        return ManagedIdentityCredential(client_id=settings.azure_client_id)
    return DefaultAzureCredential()
```

## Flask Application Structure (Alternative)

```python
# app/__init__.py
import logging
from flask import Flask

from app.config import Config


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Configure logging
    logging.basicConfig(
        level=getattr(logging, app.config.get("LOG_LEVEL", "INFO")),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # Register blueprints
    from app.routes.health import health_bp
    from app.routes.api import api_bp
    app.register_blueprint(health_bp)
    app.register_blueprint(api_bp, url_prefix="/api/v1")

    return app
```

```python
# app/config.py
import os


class Config:
    APP_NAME = os.getenv("APP_NAME", "prototype-api")
    DEBUG = os.getenv("DEBUG", "false").lower() == "true"
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
    AZURE_CLIENT_ID = os.getenv("AZURE_CLIENT_ID", "")
    AZURE_STORAGE_ENDPOINT = os.getenv("AZURE_STORAGE_ENDPOINT", "")
    AZURE_KEYVAULT_ENDPOINT = os.getenv("AZURE_KEYVAULT_ENDPOINT", "")
```

## Azure SDK Initialization with DefaultAzureCredential

```python
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient
from azure.keyvault.secrets import SecretClient
from azure.cosmos import CosmosClient
from openai import AzureOpenAI

from app.config import get_settings


_credential = None
_blob_client = None
_keyvault_client = None
_cosmos_client = None
_openai_client = None


async def init_azure_clients():
    """Initialize Azure SDK clients at startup."""
    global _credential, _blob_client, _keyvault_client, _cosmos_client, _openai_client

    settings = get_settings()

    # Credential (shared across all clients)
    _credential = DefaultAzureCredential(
        managed_identity_client_id=settings.azure_client_id or None
    )

    # Storage
    if settings.azure_storage_endpoint:
        _blob_client = BlobServiceClient(
            account_url=settings.azure_storage_endpoint,
            credential=_credential,
        )

    # Key Vault
    if settings.azure_keyvault_endpoint:
        _keyvault_client = SecretClient(
            vault_url=settings.azure_keyvault_endpoint,
            credential=_credential,
        )

    # Cosmos DB
    if settings.azure_cosmos_endpoint:
        _cosmos_client = CosmosClient(
            url=settings.azure_cosmos_endpoint,
            credential=_credential,
        )

    # Azure OpenAI
    if settings.azure_openai_endpoint:
        _openai_client = AzureOpenAI(
            azure_endpoint=settings.azure_openai_endpoint,
            azure_deployment=settings.azure_openai_deployment,
            azure_ad_token_provider=_get_token_provider(_credential),
            api_version="2024-10-21",
        )


def _get_token_provider(credential):
    """Create a token provider function for Azure OpenAI."""
    from azure.identity import get_bearer_token_provider
    return get_bearer_token_provider(
        credential, "https://cognitiveservices.azure.com/.default"
    )


async def close_azure_clients():
    """Close Azure SDK clients at shutdown."""
    global _credential
    if _credential:
        _credential.close()


def get_blob_client() -> BlobServiceClient:
    if _blob_client is None:
        raise RuntimeError("Blob client not initialized")
    return _blob_client


def get_keyvault_client() -> SecretClient:
    if _keyvault_client is None:
        raise RuntimeError("Key Vault client not initialized")
    return _keyvault_client


def get_cosmos_client() -> CosmosClient:
    if _cosmos_client is None:
        raise RuntimeError("Cosmos client not initialized")
    return _cosmos_client


def get_openai_client() -> AzureOpenAI:
    if _openai_client is None:
        raise RuntimeError("OpenAI client not initialized")
    return _openai_client
```

## Dockerfile Pattern (Multi-Stage Build)

```dockerfile
# Stage 1: Build
FROM python:3.12-slim AS builder

WORKDIR /build

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Stage 2: Runtime
FROM python:3.12-slim AS runtime

# Security: non-root user
RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application code
COPY app/ ./app/

# Set ownership
RUN chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

## Health Check Endpoints

```python
# app/routers/health.py
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Response

from app.config import get_settings

router = APIRouter()
logger = logging.getLogger(__name__)

_start_time = datetime.now(timezone.utc)


@router.get("/health")
async def health():
    """Basic health check."""
    return {"status": "healthy"}


@router.get("/healthz")
async def healthz():
    """Kubernetes-style liveness probe."""
    return Response(status_code=200)


@router.get("/readyz")
async def readyz():
    """Kubernetes-style readiness probe with dependency checks."""
    settings = get_settings()
    checks = {}
    overall_healthy = True

    # Check Azure Storage connectivity
    if settings.azure_storage_endpoint:
        try:
            from app.services.azure_clients import get_blob_client
            client = get_blob_client()
            # Lightweight call to verify connectivity
            client.get_account_information()
            checks["azure_storage"] = "healthy"
        except Exception as e:
            logger.warning("Storage health check failed: %s", e)
            checks["azure_storage"] = "unhealthy"
            overall_healthy = False

    uptime_seconds = (datetime.now(timezone.utc) - _start_time).total_seconds()

    return {
        "status": "healthy" if overall_healthy else "degraded",
        "uptime_seconds": round(uptime_seconds, 1),
        "version": settings.app_version,
        "checks": checks,
    }
```

## requirements.txt Management

```text
# Web framework
fastapi>=0.115.0
uvicorn[standard]>=0.32.0
pydantic-settings>=2.6.0

# Azure SDK (core)
azure-identity>=1.19.0

# Azure SDK (services - uncomment as needed)
# azure-storage-blob>=12.24.0
# azure-keyvault-secrets>=4.9.0
# azure-cosmos>=4.9.0
# azure-servicebus>=7.13.0
# openai>=1.58.0

# Observability
# opencensus-ext-azure>=1.1.13
# opentelemetry-api>=1.28.0
# opentelemetry-sdk>=1.28.0
```

## .env.example Pattern

```bash
# Application
APP_NAME=prototype-api
APP_VERSION=0.1.0
DEBUG=false
LOG_LEVEL=INFO

# Azure Identity (leave empty for DefaultAzureCredential chain)
AZURE_CLIENT_ID=

# Azure Service Endpoints (no secrets - just URLs)
AZURE_STORAGE_ENDPOINT=https://<storage-account>.blob.core.windows.net
AZURE_KEYVAULT_ENDPOINT=https://<keyvault-name>.vault.azure.net
AZURE_COSMOS_ENDPOINT=https://<cosmos-account>.documents.azure.com:443
AZURE_OPENAI_ENDPOINT=https://<openai-resource>.openai.azure.com
AZURE_OPENAI_DEPLOYMENT=gpt-4o

# Server
HOST=0.0.0.0
PORT=8000
```

## Logging Configuration

```python
# app/logging_config.py
import logging
import sys
from app.config import get_settings


def configure_logging():
    """Configure structured logging for the application."""
    settings = get_settings()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    # Root logger
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    # Suppress noisy Azure SDK logging
    logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)
    logging.getLogger("azure.identity").setLevel(logging.WARNING)

    # Application logger
    logger = logging.getLogger("app")
    logger.setLevel(level)

    return logger
```

### Structured JSON Logging (Production)

```python
import json
import logging
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    """JSON log formatter for container environments."""

    def format(self, record):
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry)
```

## Error Handling Patterns

```python
# app/errors.py
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import logging

logger = logging.getLogger(__name__)


class AppError(Exception):
    """Base application error."""

    def __init__(self, message: str, status_code: int = 500, detail: str = None):
        self.message = message
        self.status_code = status_code
        self.detail = detail
        super().__init__(self.message)


class NotFoundError(AppError):
    def __init__(self, resource: str, identifier: str):
        super().__init__(
            message=f"{resource} not found: {identifier}",
            status_code=404,
        )


class ValidationError(AppError):
    def __init__(self, message: str, detail: str = None):
        super().__init__(message=message, status_code=422, detail=detail)


def register_error_handlers(app: FastAPI):
    """Register global error handlers on the FastAPI app."""

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError):
        logger.warning("Application error: %s (status=%d)", exc.message, exc.status_code)
        body = {"error": exc.message}
        if exc.detail:
            body["detail"] = exc.detail
        return JSONResponse(status_code=exc.status_code, content=body)

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception):
        logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"error": "Internal server error"},
        )
```

### Azure SDK Error Handling

```python
from azure.core.exceptions import (
    HttpResponseError,
    ResourceNotFoundError,
    ClientAuthenticationError,
)
import logging

logger = logging.getLogger(__name__)


async def safe_azure_call(operation, *args, **kwargs):
    """Wrapper for Azure SDK calls with standardized error handling."""
    try:
        return await operation(*args, **kwargs) if callable(operation) else operation
    except ClientAuthenticationError as e:
        logger.error("Azure authentication failed: %s", e.message)
        raise AppError("Authentication failed", status_code=401) from e
    except ResourceNotFoundError as e:
        logger.warning("Azure resource not found: %s", e.message)
        raise NotFoundError("Azure resource", str(e)) from e
    except HttpResponseError as e:
        logger.error("Azure API error (status=%d): %s", e.status_code, e.message)
        raise AppError(
            f"Azure service error: {e.message}",
            status_code=502,
        ) from e
```

## Testing Patterns

### conftest.py
```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient


@pytest.fixture
def mock_credential():
    """Mock Azure credential for testing."""
    credential = MagicMock()
    credential.get_token = MagicMock(return_value=MagicMock(token="test-token"))
    return credential


@pytest.fixture
def mock_blob_client():
    """Mock Azure Blob Service client."""
    client = MagicMock()
    client.get_account_information = MagicMock(return_value={"sku_name": "Standard_LRS"})
    return client


@pytest.fixture
def app(mock_credential, mock_blob_client):
    """Create test application with mocked Azure dependencies."""
    with patch("app.dependencies.get_credential", return_value=mock_credential), \
         patch("app.services.azure_clients.get_blob_client", return_value=mock_blob_client):
        from app.main import create_app
        test_app = create_app()
        yield test_app


@pytest.fixture
def client(app):
    """HTTP test client."""
    return TestClient(app)
```

### Test Examples
```python
# tests/test_health.py

def test_health_returns_200(client):
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"


def test_healthz_returns_200(client):
    response = client.get("/healthz")
    assert response.status_code == 200


def test_readyz_reports_degraded_on_failure(client, mock_blob_client):
    mock_blob_client.get_account_information.side_effect = Exception("Connection refused")
    response = client.get("/readyz")
    data = response.json()
    assert data["status"] == "degraded"
    assert data["checks"]["azure_storage"] == "unhealthy"
```

### Testing Azure SDK Interactions
```python
# tests/test_api.py
from unittest.mock import MagicMock, patch


def test_upload_blob(client, mock_blob_client):
    container_client = MagicMock()
    mock_blob_client.get_container_client.return_value = container_client
    container_client.upload_blob = MagicMock()

    response = client.post(
        "/api/v1/upload",
        files={"file": ("test.txt", b"hello world", "text/plain")},
    )
    assert response.status_code == 200
    container_client.upload_blob.assert_called_once()


def test_get_secret(client):
    with patch("app.services.azure_clients.get_keyvault_client") as mock_kv:
        mock_secret = MagicMock()
        mock_secret.value = "secret-value"
        mock_kv.return_value.get_secret.return_value = mock_secret

        response = client.get("/api/v1/config/my-setting")
        assert response.status_code == 200
```

### pytest.ini / pyproject.toml Configuration
```toml
# In pyproject.toml
[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
filterwarnings = [
    "ignore::DeprecationWarning:azure.*",
]
markers = [
    "integration: marks tests requiring live Azure resources",
]
```
