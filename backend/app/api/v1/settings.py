import logging

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.core.security import hash_password, verify_password
from app.models.new_models import UserSettings
from app.models.user import User
from app.schemas.settings import ChangePasswordRequest, UserSettingsUpdate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings", tags=["settings"])


async def _get_or_create_settings(db: DbSession, user: User) -> UserSettings:
    """Return existing UserSettings or create defaults."""
    r = await db.execute(select(UserSettings).where(UserSettings.user_id == user.id))
    settings = r.scalar_one_or_none()
    if settings is None:
        settings = UserSettings(user_id=user.id)
        db.add(settings)
        await db.flush()
    return settings


@router.get("")
async def settings_get(db: DbSession, user: CurrentUser):
    """Get user settings. Creates defaults if none exist."""
    settings = await _get_or_create_settings(db, user)
    return {
        "push_notifications": settings.push_notifications,
        "sms_notifications": settings.sms_notifications,
        "email_notifications": settings.email_notifications,
        "theme": settings.theme,
        "language": settings.language,
    }


@router.put("")
async def settings_update(body: UserSettingsUpdate, db: DbSession, user: CurrentUser):
    """Update user settings fields."""
    settings = await _get_or_create_settings(db, user)

    if body.push_notifications is not None:
        settings.push_notifications = body.push_notifications
    if body.sms_notifications is not None:
        settings.sms_notifications = body.sms_notifications
    if body.email_notifications is not None:
        settings.email_notifications = body.email_notifications
    if body.theme is not None:
        settings.theme = body.theme
    if body.language is not None:
        settings.language = body.language

    await db.flush()

    return {
        "push_notifications": settings.push_notifications,
        "sms_notifications": settings.sms_notifications,
        "email_notifications": settings.email_notifications,
        "theme": settings.theme,
        "language": settings.language,
    }


@router.put("/change-password")
async def settings_change_password(body: ChangePasswordRequest, db: DbSession, user: CurrentUser):
    """Change the current user's password."""
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    if body.current_password == body.new_password:
        raise HTTPException(status_code=400, detail="New password must be different from current")

    user.password_hash = hash_password(body.new_password)
    await db.flush()

    return {"message": "Password updated successfully"}


@router.delete("/delete-account")
async def settings_delete_account(db: DbSession, user: CurrentUser):
    """Soft delete the user account."""
    user.is_active = False
    await db.flush()
    return {"message": "Account deleted successfully"}
