"""Custom SQLAlchemy column types for cross-database compatibility."""

import uuid as uuid_lib
from sqlalchemy import TypeDecorator, CHAR
from sqlalchemy.dialects.postgresql import UUID as PG_UUID


class UUID(TypeDecorator):
    """
    Platform-independent UUID type.

    Uses PostgreSQL's native UUID type when available, otherwise uses
    CHAR(36) for compatibility with SQLite and other databases.

    Stores UUIDs as UUID objects in Python.
    """
    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):
        """Use native UUID for PostgreSQL, CHAR(36) for others."""
        if dialect.name == 'postgresql':
            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        else:
            return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value, dialect):
        """Convert Python UUID to database format."""
        if value is None:
            return None
        elif dialect.name == 'postgresql':
            return value  # PostgreSQL handles UUID objects natively
        else:
            # For SQLite and others, store as string
            if isinstance(value, uuid_lib.UUID):
                return str(value)
            else:
                return str(uuid_lib.UUID(value))

    def process_result_value(self, value, dialect):
        """Convert database value to Python UUID."""
        if value is None:
            return None
        if isinstance(value, uuid_lib.UUID):
            return value
        else:
            return uuid_lib.UUID(value)
