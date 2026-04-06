# ratinglift-backend-core

Production-grade FastAPI backend for RatingLift with health checks, observability, structured logging, and modular services.

## Quick Start

### Prerequisites
- Docker & Docker Compose
- Python 3.11+ (for local development)

### 1. Start the Development Environment

```bash
docker compose -f docker-compose.dev.yml up --build
```

Services will be available at:
- **Backend**: http://localhost:8000
- **PostgreSQL**: localhost:5432
- **MongoDB**: localhost:27017
- **Redis**: localhost:6379
- **pgAdmin**: http://localhost:8080
- **Mongo Express**: http://localhost:8081

### 2. Access API Documentation

- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

### 3. Test Health Endpoints

```bash
curl http://localhost:8000/health
curl http://localhost:8000/ready
curl http://localhost:8000/live
```

## API Endpoints

### Core Health Checks
- `GET /health` - Application health status
- `GET /ready` - Readiness probe (dependencies check)
- `GET /live` - Liveness probe

### Authentication (`/auth`)
- `POST /auth/login` - User authentication
- `POST /auth/logout` - Logout
- `GET /auth/me` - Get current user info

## Architecture

### Core Features
- **FastAPI** - Modern, fast web framework
- **Async/Await** - Full async support
- **CORS Middleware** - Cross-origin resource sharing
- **Structured JSON Logging** - Production-ready logging with request/tenant context
- **Request Context Middleware** - Automatic request ID and tenant ID tracking
- **Exception Handlers** - Centralized error handling

### Modular Design
The application is organized into independent modules for scalability:

```
app/
├── main.py              # FastAPI application entry point
├── core/                # Core framework utilities
│   ├── config.py        # Settings with pydantic-settings
│   ├── logging.py       # JSON structured logging
│   ├── middleware.py    # Request context (X-Request-ID, X-Tenant-ID)
│   ├── security.py      # Password hashing & crypto
│   ├── dependencies.py  # FastAPI dependency injection
│   ├── exceptions.py    # Exception handlers
│   └── __init__.py
├── db/                  # Database layer
│   ├── base.py          # SQLAlchemy declarative base
│   ├── session.py       # Database connection & session
│   ├── models/          # ORM models
│   └── __init__.py
├── modules/             # Business logic (domain-driven)
│   ├── auth/            # Authentication & authorization
│   ├── tenant/          # Multi-tenancy support
│   ├── property/        # Property management
│   ├── review/          # Review system
│   ├── ai/              # AI/ML services
│   ├── recovery/        # Data recovery & backups
│   ├── analytics/       # Analytics & reporting
│   ├── billing/         # Payment & billing
│   ├── support/         # Support tickets
│   ├── admin/           # Admin operations
│   └── monitoring/      # System monitoring
├── shared/              # Cross-cutting utilities
│   ├── schemas.py       # Pydantic response models
│   ├── exceptions.py    # Custom exception classes
│   └── utils.py         # Utility functions
└── workers/             # Background jobs & tasks
    ├── review_worker.py # Review processing
    ├── ai_worker.py     # AI job queue
    └── posting_worker.py # Content posting queue
```

## Configuration

### Environment Variables

Create a `.env.dev` file:

```env
# Database
DATABASE_URL=postgresql://postgres:postgres@postgres:5432/ratinglift

# NoSQL
MONGO_URL=mongodb://mongo:27017/ratinglift

# Cache & Queue
REDIS_URL=redis://redis:6379/0

# Application
ENVIRONMENT=development
```

### Services in Docker Compose

| Service | Port | Purpose |
|---------|------|---------|
| Backend | 8000 | FastAPI application |
| PostgreSQL | 5432 | Relational database |
| MongoDB | 27017 | Document database |
| Redis | 6379 | Cache & queue |
| pgAdmin | 8080 | PostgreSQL management |
| Mongo Express | 8081 | MongoDB management |

## Development

### Running Tests

```bash
# Install test dependencies
pip install -r requirements.txt

# Run all tests
pytest

# Run specific test file
pytest tests/test_health.py

# Run with coverage
pytest --cov=app tests/
```

### Local Setup (without Docker)

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run the application
uvicorn app.main:app --reload
```

### Logging

The application uses **structured JSON logging** for production readiness:

```json
{
  "timestamp": "2026-04-06T12:00:00",
  "level": "INFO",
  "logger": "app.main",
  "message": "application_startup",
  "request_id": "550e8400-e29b-41d4-a716-446655440000",
  "tenant_id": "acme-corp"
}
```

Each request automatically includes:
- `request_id` - Unique request identifier (X-Request-ID header)
- `tenant_id` - Tenant context (X-Tenant-ID header)

## Production Deployment

### Security Checklist
- [ ] Update `CORS_ORIGINS` in config
- [ ] Use environment-specific `.env` files
- [ ] Enable HTTPS/TLS
- [ ] Set strong database passwords
- [ ] Configure rate limiting
- [ ] Enable API authentication
- [ ] Set up monitoring & alerting
- [ ] Configure log aggregation

### Scaling
- Use load balancer (nginx, AWS ALB)
- Scale backend instances
- Use managed databases (AWS RDS, MongoDB Atlas)
- Cache with Redis Cluster
- Set up container orchestration (Kubernetes)

## Tech Stack

- **Framework**: FastAPI 0.104+
- **Server**: Uvicorn
- **ORM**: SQLAlchemy
- **Database**: PostgreSQL + MongoDB
- **Cache**: Redis
- **Auth**: Passlib + bcrypt
- **Validation**: Pydantic v2
- **Testing**: pytest + pytest-asyncio
- **Logging**: Structured JSON
- **Containerization**: Docker

## License

MIT