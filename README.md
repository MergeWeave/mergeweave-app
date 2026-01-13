# MergeWeave GitHub App

The official GitHub App for MergeWeave - automated merge conflict detection and resolution for your repositories.

## Overview

The MergeWeave GitHub App monitors your repositories for merge conflicts and provides intelligent resolution suggestions powered by the MergeWeave Conflict Resolution Engine (CRE).

### Features

- **Automatic Conflict Detection** - Detects merge conflicts on push events before they become problems
- **Intelligent Resolution Suggestions** - AI-powered resolution recommendations with confidence scores
- **PR Integration** - Posts resolution suggestions directly as PR comments
- **Real-time Dashboard** - Monitor conflict statistics and resolution history via WebSocket updates
- **OAuth Installation Flow** - Easy one-click installation for your repositories

## Architecture

```
GitHub Webhooks
       │
       ▼
┌──────────────────┐
│  GitHub App      │──────► PostgreSQL (shared)
│  (this service)  │──────► Redis (caching)
└────────┬─────────┘──────► S3 (resolution storage)
         │
         ▼
┌──────────────────┐
│  MergeWeave      │
│  Public API      │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  Conflict        │
│  Resolution      │
│  Engine (CRE)    │
└──────────────────┘
```

## Getting Started

### Prerequisites

- Python 3.12+
- PostgreSQL 15+
- Redis 7+
- GitHub App credentials (App ID, Private Key, Webhook Secret)

### Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/MergeWeave/mergeweave-app.git
   cd mergeweave-app
   ```

2. **Create virtual environment**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment**
   ```bash
   cp .env.example .env
   # Edit .env with your configuration
   ```

5. **Run database migrations**
   ```bash
   alembic upgrade head
   ```

6. **Start the server**
   ```bash
   uvicorn app.main:app --host 0.0.0.0 --port 8000
   ```

### Configuration

See [.env.example](.env.example) for all available configuration options.

Key configuration items:
- `GITHUB_APP_ID` - Your GitHub App ID
- `GITHUB_APP_PRIVATE_KEY_PATH` - Path to your GitHub App private key (.pem file)
- `GITHUB_WEBHOOK_SECRET` - Webhook secret configured in GitHub App settings
- `DATABASE_URL` - PostgreSQL connection string
- `REDIS_URL` - Redis connection string
- `PUBLIC_API_URL` - URL of the MergeWeave Public API

## Development

### Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=app --cov-report=html

# Run specific test file
pytest tests/unit/test_webhook_verification.py
```

### Project Structure

```
app/
├── api/            # API endpoints (health, webhooks, OAuth, dashboard)
├── auth/           # GitHub App authentication (JWT, tokens)
├── cache/          # Redis caching layer
├── clients/        # External service clients (GitHub API)
├── database/       # SQLAlchemy models and migrations
├── middleware/     # HTTP middleware (auth, rate limiting, security)
├── models/         # ORM models
├── monitoring/     # Logging and metrics
├── services/       # Business logic services
├── utils/          # Utility functions
├── webhooks/       # Webhook handlers and verification
├── workers/        # Background task processing
├── config.py       # Configuration management
└── main.py         # Application entry point
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/health/detailed` | GET | Detailed health with dependencies |
| `/webhook` | POST | GitHub webhook receiver |
| `/oauth/callback` | GET | OAuth installation callback |
| `/api/v1/dashboard/*` | GET | Dashboard analytics endpoints |
| `/metrics` | GET | Prometheus metrics |

## Documentation

- [Testing Quickstart](docs/TESTING_QUICKSTART.md)
- [Implementation Status](docs/IMPLEMENTATION_STATUS.md)

## Security

This application handles sensitive GitHub data. Security measures include:

- HMAC-SHA256 webhook signature verification
- JWT-based GitHub App authentication
- Rate limiting on all endpoints
- Security headers (CSP, X-Frame-Options, etc.)
- Input validation on all API endpoints

**Reporting Security Issues**: Please report security vulnerabilities to security@mergeweave.cloud

## License

Copyright (c) 2024-2025 MergeWeave Inc. All rights reserved.

This software is proprietary and confidential. Unauthorized copying, modification,
distribution, or use of this software, via any medium, is strictly prohibited.

## Links

- [MergeWeave Website](https://mergeweave.cloud)
- [Documentation](https://docs.mergeweave.cloud)
- [GitHub App Installation](https://github.com/apps/mergeweave)
