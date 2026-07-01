import logging
import secrets

from fastapi import APIRouter, HTTPException
from sqlalchemy import select, func

from app.api.deps import CurrentUser, DbSession
from app.models.new_models import ReferralCode, ReferralReward
from app.schemas.referral import ApplyReferralRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/referral", tags=["referral"])


def _generate_referral_code() -> str:
    """Generate a short unique referral code."""
    return secrets.token_hex(4).upper()  # 8-char hex string


@router.get("/my-code")
async def referral_get_my_code(db: DbSession, user: CurrentUser):
    """Get or create referral code for the current user."""
    r = await db.execute(select(ReferralCode).where(ReferralCode.user_id == user.id))
    ref_code = r.scalar_one_or_none()

    if ref_code is None:
        # Generate a unique code
        code = _generate_referral_code()
        while True:
            existing = await db.execute(select(ReferralCode).where(ReferralCode.code == code))
            if existing.scalar_one_or_none() is None:
                break
            code = _generate_referral_code()

        ref_code = ReferralCode(
            user_id=user.id,
            code=code,
            reward_balance=0,
            total_referrals=0,
        )
        db.add(ref_code)
        await db.flush()

    return {
        "code": ref_code.code,
        "reward_balance": ref_code.reward_balance,
        "total_referrals": ref_code.total_referrals,
        "referral_link": f"ref={ref_code.code}",
    }


@router.post("/apply")
async def referral_apply(body: ApplyReferralRequest, db: DbSession, user: CurrentUser):
    """Apply a referral code. Creates a pending reward for the referrer."""
    # Cannot apply your own code
    code_str = body.code.strip().upper()

    ref_r = await db.execute(select(ReferralCode).where(ReferralCode.code == code_str))
    referrer_code = ref_r.scalar_one_or_none()

    if referrer_code is None:
        raise HTTPException(status_code=404, detail="Invalid referral code")

    if referrer_code.user_id == user.id:
        raise HTTPException(status_code=400, detail="Cannot apply your own referral code")

    # Check if this user has already been referred
    existing_r = await db.execute(
        select(ReferralReward).where(ReferralReward.referred_user_id == user.id)
    )
    if existing_r.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Referral already applied")

    # Create pending reward
    reward_amount = 50  # Default reward amount
    reward = ReferralReward(
        referrer_user_id=referrer_code.user_id,
        referred_user_id=user.id,
        referred_email=user.email,
        amount=reward_amount,
        status="pending",
    )
    db.add(reward)
    await db.flush()

    return {
        "message": "Referral applied successfully",
        "reward_id": str(reward.id),
        "status": "pending",
    }


@router.get("/history")
async def referral_history(db: DbSession, user: CurrentUser):
    """Get referral reward history for the current user as referrer."""
    r = await db.execute(
        select(ReferralReward)
        .where(ReferralReward.referrer_user_id == user.id)
        .order_by(ReferralReward.created_at.desc())
    )
    rewards = r.scalars().all()

    total_earned = sum(rw.amount for rw in rewards if rw.status == "paid")
    pending_amount = sum(rw.amount for rw in rewards if rw.status == "pending")

    return {
        "rewards": [
            {
                "id": str(rw.id),
                "referred_email": rw.referred_email,
                "amount": rw.amount,
                "status": rw.status,
                "created_at": rw.created_at.isoformat() if rw.created_at else None,
            }
            for rw in rewards
        ],
        "total_earned": total_earned,
        "pending_amount": pending_amount,
    }
