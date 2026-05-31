from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import func, select, update

from app.api.deps import CurrentUser, DbSession
from app.core.limiter import limiter
from app.models.notification import Notification
from app.schemas.notification import NotificationResponse, NotificationUpdate

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=dict)
async def get_notifications(
    db: DbSession,
    user: CurrentUser,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    r = await db.execute(
        select(Notification)
        .where(Notification.user_id == user.id)
        .order_by(Notification.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    notifications = r.scalars().all()

    c = await db.execute(
        select(func.count(Notification.id)).where(Notification.user_id == user.id, Notification.read == False)
    )
    unread_count = c.scalar() or 0

    return {
        "notifications": [NotificationResponse.model_validate(n).model_dump() for n in notifications],
        "unread_count": unread_count,
    }


@router.patch("/{notif_id}", response_model=NotificationResponse)
@limiter.limit("30/minute")
async def update_notification(
    request: Request,
    notif_id: UUID,
    body: NotificationUpdate,
    db: DbSession,
    user: CurrentUser,
):
    r = await db.execute(
        select(Notification).where(Notification.id == notif_id, Notification.user_id == user.id)
    )
    notif = r.scalar_one_or_none()
    if not notif:
        raise HTTPException(status_code=404, detail="Notification not found")
    notif.read = body.read
    await db.flush()
    await db.refresh(notif)
    
    # Broadcast new unread count via web socket
    try:
        from app.ws.manager import manager as ws_manager
        c = await db.execute(
            select(func.count(Notification.id)).where(Notification.user_id == user.id, Notification.read == False)
        )
        await ws_manager.send_json_to_user(str(user.id), {
            "type": "NOTIFICATION_UPDATE",
            "payload": {
                "unreadCount": c.scalar() or 0
            }
        })
    except Exception:
        pass
        
    return notif


@router.patch("/read/all", response_model=dict)
@limiter.limit("10/minute")
async def mark_all_read(request: Request, db: DbSession, user: CurrentUser):
    # Note: no `request` param needed — uses raw DB operation
    await db.execute(
        update(Notification)
        .where(Notification.user_id == user.id, Notification.read == False)
        .values(read=True)
    )
    await db.flush()
    try:
        from app.ws.manager import manager as ws_manager
        await ws_manager.send_json_to_user(str(user.id), {
            "type": "NOTIFICATION_UPDATE",
            "payload": {
                "unreadCount": 0
            }
        })
    except Exception:
        pass
    return {"ok": True}
