from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
from pathlib import Path
from app.core.config import settings
from app.core.logging import setup_logging, get_logger
from app.core.middleware import RequestContextMiddleware, TenantContextMiddleware
from app.core.exceptions import register_exception_handlers
from app.modules.auth.routes import router as auth_router
from app.modules.admin.audit_routes import admin_audit_router
from app.modules.admin.property_routes import admin_property_router
from app.modules.admin.connectors import admin_connector_router
from app.modules.admin.property_connectors import admin_property_connector_router
from app.modules.connectors import tenant_connector_router
from app.modules.property import property_router
from app.modules.property_connector import property_connector_router
from sqlalchemy import text

setup_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("application_startup")
    from app.db.base import Base
    from app.db.session import engine
    Base.metadata.create_all(bind=engine)
    logger.info("database_tables_created")

    # Run DB seeders (idempotent, safe on every startup)
    try:
        from app.db.seed import run_seeders
        run_seeders()
    except Exception as exc:  # pragma: no cover - best-effort seeding
        logger.error("seeders_failed error=%s", exc)

    try:
        yield
    finally:
        logger.info("application_shutdown")
        from app.db.mongo import close_mongo_connection
        close_mongo_connection()
        logger.info("database_connections_closed")


app = FastAPI(
    title="ratinglift-backend-core",
    version="0.1.0",
    description="Core backend for RatingLift with health checks, observability, and modular services.",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.add_middleware(RequestContextMiddleware)
app.add_middleware(TenantContextMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_exception_handlers(app)

# Serve uploaded media (logos, etc.) from a local volume — only when the
# storage backend is the local filesystem. With ``STORAGE_BACKEND=s3`` the
# files live in the bucket and are served directly (or via CDN), so the
# app should not also expose ``/media``.
if (settings.storage_backend or "local").lower() == "local":
    _media_dir = Path(settings.media_root).resolve()
    _media_dir.mkdir(parents=True, exist_ok=True)
    app.mount(
        settings.media_url_prefix,
        StaticFiles(directory=str(_media_dir)),
        name="media",
    )

# Include routers
app.include_router(auth_router)
app.include_router(admin_audit_router)
app.include_router(admin_property_router)
app.include_router(admin_connector_router)
app.include_router(admin_property_connector_router)
app.include_router(tenant_connector_router)
app.include_router(property_router)
app.include_router(property_connector_router)

@app.get("/")
async def root():
    """Redirect to API documentation"""
    return RedirectResponse(url="/docs")

@app.get("/health")
async def health():
    """Health check endpoint"""
    from app.db.session import engine
    from app.db.mongo import client as mongo_client
    from app.db.redis import ping_redis

    health_status = {"status": "ok", "databases": {}}

    # Check PostgreSQL
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        health_status["databases"]["postgresql"] = "ok"
    except Exception as e:
        health_status["databases"]["postgresql"] = f"error: {str(e)}"
        health_status["status"] = "degraded"

    # Check MongoDB
    try:
        mongo_client.admin.command('ping')
        health_status["databases"]["mongodb"] = "ok"
    except Exception as e:
        health_status["databases"]["mongodb"] = f"error: {str(e)}"
        health_status["status"] = "degraded"

    # Check Redis
    try:
        if ping_redis():
            health_status["databases"]["redis"] = "ok"
        else:
            health_status["databases"]["redis"] = "error: ping failed"
            health_status["status"] = "degraded"
    except Exception as e:
        health_status["databases"]["redis"] = f"error: {str(e)}"
        health_status["status"] = "degraded"

    return health_status

@app.get("/ready")
async def ready():
    """Readiness check endpoint"""
    return {"status": "ok"}

@app.get("/live")
async def live():
    """Liveness check endpoint"""
    return {"status": "ok"}
