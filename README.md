# ratinglift-backend-core

Production-grade FastAPI backend for **RatingLift** — a multi-tenant reputation, review, and AI automation platform.
Includes authentication (password, social, MFA/TOTP, sessions), RBAC, multi-database persistence (PostgreSQL + MongoDB + Redis), Alembic migrations, structured JSON logging, and a modular domain-driven architecture.

---

## Quick Start

### Prerequisites
- Docker & Docker Compose
- Python 3.11+ (for local development)

### 1. Configure environment

Create a `.env.dev` file at the repo root (used by [docker-compose.dev.yml](docker-compose.dev.yml)):

```env
# Application
ENVIRONMENT=development
SECRET_KEY=change-me

# PostgreSQL
DATABASE_URL=postgresql://postgres:postgres@postgres:5432/ratinglift

# MongoDB
MONGO_URL=mongodb://admin:admin123@mongo:27017/ratinglift?authSource=admin

# Redis
REDIS_URL=redis://redis:6379/0

# Mail (MailHog in dev)
SMTP_HOST=mailhog
SMTP_PORT=1025

# CORS
CORS_ORIGINS=http://localhost:3000
```

### 2. Start the dev stack

```bash
docker compose -f docker-compose.dev.yml up --build
```

The backend container automatically waits for PostgreSQL, runs `alembic upgrade head`, and then starts Uvicorn with hot-reload.

### 3. Open the API

- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc
- **Health**: http://localhost:8000/health

---

## Services

| Service           | URL / Port             | Purpose                                        |
|-------------------|------------------------|------------------------------------------------|
| Backend (FastAPI) | http://localhost:8000  | API + docs                                     |
| PostgreSQL        | `localhost:5432`       | Relational store                               |
| MongoDB           | `localhost:27017`      | Document store                                 |
| Redis             | `localhost:6379`       | Cache, queues, token blacklist                 |
| pgAdmin           | http://localhost:8080  | PostgreSQL UI (`admin@admin.com` / `admin`)    |
| Adminer           | http://localhost:8085  | Lightweight DB UI                              |
| Mongo Express     | http://localhost:8088  | MongoDB UI (`admin` / `admin123`)              |
| Redis Commander   | http://localhost:8082  | Redis UI                                       |
| MailHog (SMTP)    | `localhost:1025`       | Catches outgoing email in dev                  |
| MailHog (UI)      | http://localhost:8025  | View captured emails                           |

---

## API Endpoints

### Health
- `GET /health` — application + dependency status
- `GET /ready` — readiness probe
- `GET /live` — liveness probe

### Authentication (`/api/v1/auth`)

| Method | Path                              | Description                           |
|--------|-----------------------------------|---------------------------------------|
| POST   | `/signup`                         | Create user + tenant                  |
| POST   | `/login`                          | Password login                        |
| POST   | `/refresh`                        | Refresh access token                  |
| POST   | `/logout`                         | Revoke current token / refresh        |
| GET    | `/me`                             | Current user profile                  |
| POST   | `/social/google`                  | Google OAuth login                    |
| POST   | `/social/microsoft`               | Microsoft OAuth login                 |
| POST   | `/social/facebook`                | Facebook OAuth login                  |
| POST   | `/password/forgot`                | Request password reset                |
| POST   | `/password/reset`                 | Complete password reset               |
| POST   | `/email/verify`                   | Verify email with token               |
| POST   | `/email/resend`                   | Resend verification email             |
| GET    | `/mfa/status`                     | MFA configuration                     |
| POST   | `/mfa/channel`                    | Add MFA channel (email/SMS)           |
| POST   | `/mfa/channel/verify`             | Verify added channel                  |
| POST   | `/mfa/enable` / `/mfa/disable`    | Toggle MFA                            |
| POST   | `/mfa/verify`                     | Complete MFA challenge during login   |
| POST   | `/mfa/totp/setup`                 | Begin TOTP enrollment (QR/secret)     |
| POST   | `/mfa/totp/verify`                | Confirm TOTP code                     |
| GET    | `/sessions`                       | List active sessions                  |
| POST   | `/sessions/{id}/revoke`           | Revoke a specific session             |

### Admin Auth (`/api/v1/admin/auth`)
- `POST /login` — admin-only login (rejects non-admin roles)
- `POST /create-admin` — provision an admin (requires admin role)

---

## Architecture

```
app/
├── main.py              # FastAPI entry point + lifespan + health
├── core/                # Framework infrastructure
│   ├── config.py        # pydantic-settings configuration
│   ├── logging.py       # JSON structured logging
│   ├── middleware.py    # X-Request-ID / X-Tenant-ID context
│   ├── security.py      # Password hashing, crypto helpers
│   ├── dependencies.py  # Shared FastAPI dependencies
│   └── exceptions.py    # Centralized exception handlers
├── db/
│   ├── base.py          # SQLAlchemy declarative base
│   ├── session.py       # Engine + SessionLocal
│   ├── mongo.py         # Mongo client
│   ├── redis.py         # Redis client
│   ├── seed.py          # Idempotent startup seeders
│   └── models/          # ORM models (user, tenant, property,
│                        #   subscription, invoice, connector,
│                        #   property_connector, login_session,
│                        #   audit_log)
├── modules/             # Domain-driven business modules
│   ├── auth/            # Routes, service, tokens, MFA, TOTP,
│   │                    #   OAuth, password reset, validators,
│   │                    #   senders, bootstrap
│   ├── tenant/          # Multi-tenancy
│   ├── property/        # Property management
│   ├── review/          # Reviews ingestion / responses
│   ├── ai/              # AI/ML services
│   ├── analytics/       # Analytics & reporting
│   ├── billing/         # Subscriptions & invoices
│   ├── support/         # Support tickets
│   ├── recovery/        # Backup & recovery
│   ├── admin/           # Admin operations
│   └── monitoring/      # System monitoring
├── shared/              # Cross-cutting schemas, utils, errors
└── workers/             # Background workers
    ├── review_worker.py
    ├── ai_worker.py
    └── posting_worker.py

alembic/                 # Database migrations
tests/                   # pytest test suite
```

### Key features
- **FastAPI** with async lifespan, CORS, and centralized exception handlers
- **Multi-database**: PostgreSQL (SQLAlchemy + Alembic), MongoDB (PyMongo), Redis
- **Auth stack**: bcrypt password hashing, JWT (access + refresh), session tracking, MFA channels, TOTP (`pyotp`), social login (Google / Microsoft / Facebook), password reset, email verification, RBAC via `require_role`
- **Audit log** + **login session** tracking with IP, device, and location capture
- **Twilio + SMTP** senders (MailHog used in dev)
- **Structured JSON logs** with `request_id` and `tenant_id` context

---

## Development

### Local setup (without Docker)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Apply migrations against your local DB
alembic upgrade head

# Run the API
uvicorn app.main:app --reload
```

### Database migrations

```bash
# Create a new migration after model changes
alembic revision --autogenerate -m "describe change"

# Apply migrations
alembic upgrade head

# Roll back one revision
alembic downgrade -1
```

### Tests

```bash
pytest                                  # full suite
pytest tests/test_auth_integration.py   # single file
pytest --cov=app tests/                 # with coverage
```

Existing test modules:
- [tests/test_health.py](tests/test_health.py) — health endpoints
- [tests/test_auth_integration.py](tests/test_auth_integration.py) — end-to-end auth flows
- [tests/test_auth_extensions.py](tests/test_auth_extensions.py) — MFA / TOTP / sessions
- [tests/test_auth_validation.py](tests/test_auth_validation.py) — schema validation
- [tests/test_seed.py](tests/test_seed.py) — seeders

### Logging

All logs are emitted as JSON, e.g.:

```json
{
  "timestamp": "2026-04-18T12:00:00Z",
  "level": "INFO",
  "logger": "app.main",
  "message": "application_startup",
  "request_id": "550e8400-e29b-41d4-a716-446655440000",
  "tenant_id": "acme-corp"
}
```

Each request automatically includes `request_id` (from / generated for `X-Request-ID`) and `tenant_id` (from `X-Tenant-ID`).

---

## Tech Stack

- **Framework**: FastAPI + Uvicorn
- **ORM / Migrations**: SQLAlchemy + Alembic
- **Databases**: PostgreSQL 15, MongoDB 6, Redis 7
- **Auth**: bcrypt, PyJWT, pyotp (TOTP), Twilio (SMS), email-validator
- **HTTP**: httpx, requests
- **Validation**: Pydantic v2 + pydantic-settings
- **Testing**: pytest, pytest-asyncio
- **Containerization**: Docker + Docker Compose

---

## File storage (logos & uploads)

The connector logo upload flow (and any future media upload) goes through
a pluggable storage backend selected by the `STORAGE_BACKEND` env var.

### Local (default — recommended for dev)

```env
STORAGE_BACKEND=local
MEDIA_ROOT=media
MEDIA_URL_PREFIX=/media
```

Files are written to `./media/` and served by FastAPI at `/media/...`.
Nothing else to configure.

### S3 (production)

```env
STORAGE_BACKEND=s3
S3_BUCKET=ratinglift-media-prod
S3_REGION=us-east-1
S3_URL_BASE=https://cdn.ratinglift.com   # optional CloudFront/custom domain
S3_KEY_PREFIX=prod                       # optional namespace per env
AWS_ACCESS_KEY_ID=...                    # or use IAM role on the host
AWS_SECRET_ACCESS_KEY=...
```

Switching is purely a config change — **no code changes required**. The
`/media` static mount is automatically disabled when `STORAGE_BACKEND=s3`,
so the bucket (or CloudFront in front of it) is the only place files are
served from.

### Migrating existing local files to S3

After flipping `STORAGE_BACKEND` to `s3` and setting the `S3_*` vars,
run the one-shot backfill **before restarting** the production app so
no live request sees a broken `/media/...` URL:

```bash
python -m app.scripts.backfill_logos_to_storage
```

The script:

- Walks every `connectors` row whose `logo_url` still points at the old
  local mount.
- Uploads the file bytes to the active backend (S3).
- Rewrites `logo_url` to the new public URL returned by the backend.
- Is idempotent — re-running skips rows that already point at S3.

You can drop the local `./media` volume from your container/compose file
once the backfill has run successfully.

---

## Production Checklist

- [ ] Set strong `SECRET_KEY` and database credentials
- [ ] Restrict `CORS_ORIGINS` to known frontends
- [ ] Terminate TLS at the load balancer (nginx / ALB)
- [ ] Use managed datastores (RDS, MongoDB Atlas, ElastiCache)
- [ ] Configure real SMTP / Twilio credentials (replace MailHog)
- [ ] Configure OAuth client IDs/secrets for Google, Microsoft, Facebook
- [ ] Enable rate limiting and WAF
- [ ] Centralize logs (ELK / Loki / CloudWatch) and metrics
- [ ] Run `alembic upgrade head` in deploy pipeline
- [ ] If switching to S3 storage: set `STORAGE_BACKEND=s3` + `S3_*` vars,
      then run `python -m app.scripts.backfill_logos_to_storage` before
      restarting the app. Remove the `./media` volume after.
- [ ] Container orchestration (Kubernetes / ECS) with health & readiness probes

---

## License

MIT
