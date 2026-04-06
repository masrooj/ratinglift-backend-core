from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from app.core.config import settings
from app.core.logging import setup_logging, get_logger
from app.core.middleware import RequestContextMiddleware
from app.core.exceptions import register_exception_handlers
from app.modules.auth.router import router as auth_router

setup_logging()
logger = get_logger(__name__)

app = FastAPI(
    title="ratinglift-backend-core",
    version="0.1.0",
    description="Core backend for RatingLift with health checks, observability, and modular services.",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(RequestContextMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_exception_handlers(app)

# Include routers
app.include_router(auth_router)

@app.on_event("startup")
async def startup_event():
    logger.info("application_startup")

@app.on_event("shutdown")
async def shutdown_event():
    logger.info("application_shutdown")

@app.get("/")
async def root():
    """Redirect to API documentation"""
    return RedirectResponse(url="/docs")

@app.get("/health")
async def health():
    """Health check endpoint"""
    return {"status": "ok"}

@app.get("/ready")
async def ready():
    """Readiness check endpoint"""
    return {"status": "ok"}

@app.get("/live")
async def live():
    """Liveness check endpoint"""
    return {"status": "ok"}
