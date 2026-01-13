"""
MergeWeave GitHub App Webhook Service

**Configuration:**
- Port: 8000 (localhost)
- Domain: webhook.mergeweave.cloud
- Webhook URL: https://webhook.mergeweave.cloud/webhook

**Status:** Placeholder (MVP)
- Accepts webhook events
- Logs event metadata
- Returns 200 OK

**Post-MVP Implementation:**
- Webhook signature verification
- GitHub App JWT/installation token management  
- CRE integration for conflict resolution
- PR status/comment updates

**Architecture:**
```
webhook.mergeweave.cloud (Apache :443)
         ↓
localhost:8000 (this FastAPI service)
         ↓
CRE Service (shared with public API)
```

**Design Principle:**
Each service gets its own subdomain:
- api.mergeweave.cloud → Public API (port 5000)
- webhook.mergeweave.cloud → GitHub App (port 8000)
- app.mergeweave.cloud → Dashboard (port 3000, future)
"""

__version__ = "0.1.0-placeholder"

