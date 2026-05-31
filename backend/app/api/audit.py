import functools
import logging
from typing import Any

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.audit_log import AuditLog
from app.models.user import User

logger = logging.getLogger(__name__)


async def _get_db():
    async for session in get_db():
        yield session


def audit_log(
    action: str,
    entity_type: str,
    entity_id_arg: str = "entity_id",
):
    """Decorator to log admin mutations to the audit trail.

    Usage:
        @router.patch("/mechanic/{mechanic_id}/verify")
        @audit_log("verify_mechanic", "mechanic", entity_id_arg="mechanic_id")
        async def verify_mechanic(mechanic_id: UUID, ...):

    Args:
        action: Human-readable action name (e.g., "verify_mechanic", "suspend_user")
        entity_type: Type of entity being modified (e.g., "mechanic", "user", "dispute")
        entity_id_arg: The name of the route parameter holding the entity ID
    """
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            # Extract admin user from kwargs (injected by FastAPI Depends)
            admin_user: User | None = None
            for val in kwargs.values():
                if isinstance(val, User):
                    admin_user = val
                    break

            entity_id = str(kwargs.get(entity_id_arg, "")) if entity_id_arg in kwargs else None

            # Execute the original function
            result = await func(*args, **kwargs)

            # Write audit log asynchronously (fire-and-forget, won't block response)
            try:
                async for session in _get_db():
                    log_entry = AuditLog(
                        user_id=admin_user.id if admin_user else None,
                        action=action,
                        entity_type=entity_type,
                        entity_id=entity_id,
                        summary=f"{action} on {entity_type} {entity_id or ''}",
                    )
                    session.add(log_entry)
                    await session.commit()
            except Exception:
                logger.warning("Failed to write audit log for %s %s", action, entity_id, exc_info=True)

            return result
        return wrapper
    return decorator
