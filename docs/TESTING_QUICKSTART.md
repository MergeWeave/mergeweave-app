# GitHub App Testing Quick Reference

## Start the Service

```bash
cd /home/tyler/Documents/projects/mw_conflict_resolution_engine/github-app
source ../venv/bin/activate
export PYTHONPATH=/home/tyler/Documents/projects/mw_conflict_resolution_engine/github-app
nohup python -m app.main > /tmp/github-app.log 2>&1 &
```

## Quick Health Checks

```bash
# Basic health
curl http://localhost:8000/health | jq .

# Detailed health (all dependencies)
curl http://localhost:8000/health/detailed | jq .

# Service info
curl http://localhost:8000/ | jq .

# Prometheus metrics
curl http://localhost:8000/metrics
```

## Test GitHub App JWT

```bash
source ../venv/bin/activate
python -c "
from app.config import config
from app.auth.github_jwt import GitHubJWTGenerator
with open(config.github_app_private_key_path, 'r') as f:
    private_key = f.read()
generator = GitHubJWTGenerator(config.github_app_id, private_key)
jwt_token = generator.generate_jwt()
print(f'JWT: {jwt_token[:80]}...')
"
```

## Test Webhook Signature Verification

```bash
source ../venv/bin/activate
python -c "
from app.webhooks.verification import verify_webhook_signature
import hmac, hashlib
secret = 'test_secret'
payload = b'{\"test\": \"data\"}'
sig = 'sha256=' + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
try:
    verify_webhook_signature(payload, sig, secret)
    print('✓ Signature verification passed')
except:
    print('✗ Signature verification failed')
"
```

## Send Test Webhook

```bash
# Generate test webhook
python -c "
import json, hmac, hashlib
from app.config import config
payload = json.dumps({'action': 'push', 'repository': {'id': 123}})
sig = 'sha256=' + hmac.new(config.github_webhook_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
print(f'SIGNATURE={sig}')
print(payload)
" > /tmp/webhook_test.sh

# Send webhook
source /tmp/webhook_test.sh
curl -X POST http://localhost:8000/webhook \
  -H "Content-Type: application/json" \
  -H "X-GitHub-Event: push" \
  -H "X-Hub-Signature-256: $SIGNATURE" \
  -d "$payload" | jq .
```

## Check Logs

```bash
# Live tail
tail -f /tmp/github-app.log

# Recent logs
tail -100 /tmp/github-app.log

# Search for errors
grep -i error /tmp/github-app.log
```

## Stop the Service

```bash
pkill -f "python -m app.main"
```

## API Documentation

- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc
- OpenAPI spec: http://localhost:8000/openapi.json

## Database Operations

```bash
# Check current migration
source ../venv/bin/activate
PYTHONPATH=. alembic current

# Run migrations
PYTHONPATH=. alembic upgrade head

# Create new migration
PYTHONPATH=. alembic revision --autogenerate -m "description"
```

## Common Issues

### Issue: Module not found
**Solution:** Set PYTHONPATH before running commands
```bash
export PYTHONPATH=/home/tyler/Documents/projects/mw_conflict_resolution_engine/github-app
```

### Issue: Database connection error
**Solution:** Check PostgreSQL is running on port 5433
```bash
docker ps | grep postgres
```

### Issue: Redis connection error
**Solution:** Check Redis is running on port 6380
```bash
docker ps | grep redis
```

### Issue: Private key not found
**Solution:** Verify the path in .env matches actual location
```bash
ls -l $(grep GITHUB_APP_PRIVATE_KEY_PATH .env | cut -d= -f2)
```

## Configuration Files

- Service config: `github-app/.env`
- Alembic config: `github-app/alembic.ini`
- Requirements: `github-app/requirements.txt`
- Main app: `github-app/app/main.py`

## Port Reference

| Service | Port | Status Check |
|---------|------|--------------|
| GitHub App | 8000 | `curl http://localhost:8000/health` |
| PostgreSQL | 5433 | `pg_isready -h localhost -p 5433` |
| Redis | 6380 | `redis-cli -p 6380 ping` |
| Public API | 5000 | `curl http://localhost:5000/health` |

