import logging

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.api.deps import DbSession
from app.models.new_models import FAQ

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/faq", tags=["faq"])


@router.get("")
async def faq_list(
    db: DbSession,
    category: str | None = Query(None, max_length=64),
):
    """List all active FAQs, optionally filtered by category."""
    q = select(FAQ).where(FAQ.active.is_(True))

    if category:
        q = q.where(FAQ.category == category)

    q = q.order_by(FAQ.order.asc())
    r = await db.execute(q)
    faqs = r.scalars().all()

    return {
        "faqs": [
            {
                "id": str(faq.id),
                "question": faq.question,
                "answer": faq.answer,
                "category": faq.category,
                "order": faq.order,
            }
            for faq in faqs
        ]
    }
