import logging
import uuid

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select, func

from app.api.deps import CurrentUser, DbSession
from app.models.new_models import SupportTicket
from app.schemas.ticket import TicketCreate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tickets", tags=["tickets"])


def _generate_ticket_number() -> str:
    """Generate a unique ticket number like TKT-<8-char hex>."""
    return "TKT-" + uuid.uuid4().hex[:8].upper()


@router.post("")
async def ticket_create(body: TicketCreate, db: DbSession, user: CurrentUser):
    """Create a new support ticket."""
    ticket_number = _generate_ticket_number()
    while True:
        existing = await db.execute(
            select(SupportTicket).where(SupportTicket.ticket_number == ticket_number)
        )
        if existing.scalar_one_or_none() is None:
            break
        ticket_number = _generate_ticket_number()

    ticket = SupportTicket(
        user_id=user.id,
        subject=body.subject,
        message=body.message,
        category=body.category,
        priority=body.priority,
        status="open",
        ticket_number=ticket_number,
    )
    db.add(ticket)
    await db.flush()

    return {
        "id": str(ticket.id),
        "ticket_number": ticket.ticket_number,
        "subject": ticket.subject,
        "message": ticket.message,
        "category": ticket.category,
        "status": ticket.status,
        "priority": ticket.priority,
        "created_at": ticket.created_at.isoformat() if ticket.created_at else None,
        "updated_at": ticket.updated_at.isoformat() if ticket.updated_at else None,
        "resolved_at": ticket.resolved_at.isoformat() if ticket.resolved_at else None,
    }


@router.get("")
async def ticket_list(
    db: DbSession,
    user: CurrentUser,
    status: str | None = Query(None, max_length=32),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
):
    """List support tickets for the current user."""
    base_q = select(SupportTicket).where(SupportTicket.user_id == user.id)
    count_q = select(func.count(SupportTicket.id)).where(SupportTicket.user_id == user.id)

    if status:
        base_q = base_q.where(SupportTicket.status == status)
        count_q = count_q.where(SupportTicket.status == status)

    total_r = await db.execute(count_q)
    total = total_r.scalar() or 0

    q = base_q.order_by(SupportTicket.created_at.desc()).offset(skip).limit(limit)
    r = await db.execute(q)
    tickets = r.scalars().all()

    return {
        "tickets": [
            {
                "id": str(t.id),
                "ticket_number": t.ticket_number,
                "subject": t.subject,
                "message": t.message,
                "category": t.category,
                "status": t.status,
                "priority": t.priority,
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "updated_at": t.updated_at.isoformat() if t.updated_at else None,
                "resolved_at": t.resolved_at.isoformat() if t.resolved_at else None,
            }
            for t in tickets
        ],
        "total": total,
    }


@router.get("/{ticket_id}")
async def ticket_get(ticket_id: str, db: DbSession, user: CurrentUser):
    """Get a single support ticket by ID."""
    try:
        ticket_uuid = uuid.UUID(ticket_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ticket ID")

    r = await db.execute(
        select(SupportTicket).where(
            SupportTicket.id == ticket_uuid,
            SupportTicket.user_id == user.id,
        )
    )
    ticket = r.scalar_one_or_none()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    return {
        "id": str(ticket.id),
        "ticket_number": ticket.ticket_number,
        "subject": ticket.subject,
        "message": ticket.message,
        "category": ticket.category,
        "status": ticket.status,
        "priority": ticket.priority,
        "created_at": ticket.created_at.isoformat() if ticket.created_at else None,
        "updated_at": ticket.updated_at.isoformat() if ticket.updated_at else None,
        "resolved_at": ticket.resolved_at.isoformat() if ticket.resolved_at else None,
    }
