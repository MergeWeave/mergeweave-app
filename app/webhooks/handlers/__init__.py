"""
Webhook Handlers.

Import all handlers to ensure they're registered via @webhook_handler decorator.
"""

# Import all handlers to register them
from app.webhooks.handlers import installation
from app.webhooks.handlers import push
from app.webhooks.handlers import check_run

__all__ = ["installation", "push", "check_run"]
