from sqlalchemy import select

import stripe
from fastapi import APIRouter, HTTPException
from razorpay import Client as RazorpayClient

from app.api.deps import CurrentUser, DbSession
from app.core.config import get_settings
from app.models.job import Job
from app.models.payment import Payment
from app.schemas.payment import PaymentCreateRequest, PaymentVerifyRequest

router = APIRouter(prefix="/payments", tags=["payments"])


@router.post("/create")
async def payment_create(body: PaymentCreateRequest, db: DbSession, user: CurrentUser):
    settings = get_settings()
    r = await db.execute(select(Job).where(Job.id == body.job_id))
    job = r.scalar_one_or_none()
    if not job or job.user_id != user.id:
        raise HTTPException(status_code=404, detail="Job not found")

    if body.provider == "stripe":
        if not settings.stripe_secret_key:
            raise HTTPException(status_code=503, detail="Stripe not configured")
        stripe.api_key = settings.stripe_secret_key
        cur = body.currency.lower()
        intent = stripe.PaymentIntent.create(
            amount=body.amount,
            currency=cur,
            metadata={"job_id": str(body.job_id), "user_id": str(user.id)},
        )
        pay = Payment(
            job_id=job.id,
            provider="stripe",
            external_id=intent.id,
            amount=body.amount / 100.0,
            currency=body.currency.upper(),
            status="pending",
            raw_payload={"client_secret": intent.client_secret},
        )
        db.add(pay)
        await db.flush()
        return {"provider": "stripe", "client_secret": intent.client_secret, "payment_id": str(pay.id)}

    if not settings.razorpay_key_id or not settings.razorpay_key_secret:
        raise HTTPException(status_code=503, detail="Razorpay not configured")
    rz = RazorpayClient(auth=(settings.razorpay_key_id, settings.razorpay_key_secret))
    order = rz.order.create(
        {
            "amount": body.amount,
            "currency": body.currency.upper(),
            "payment_capture": 1,
            "receipt": str(job.id).replace("-", "")[:40],
        }
    )
    pay = Payment(
        job_id=job.id,
        provider="razorpay",
        external_id=order["id"],
        amount=body.amount / 100.0,
        currency=body.currency.upper(),
        status="pending",
        raw_payload=order,
    )
    db.add(pay)
    await db.flush()
    return {"provider": "razorpay", "order": order, "payment_id": str(pay.id), "key_id": settings.razorpay_key_id}


@router.post("/verify")
async def payment_verify(body: PaymentVerifyRequest, db: DbSession, user: CurrentUser):
    settings = get_settings()
    r = await db.execute(select(Job).where(Job.id == body.job_id))
    job = r.scalar_one_or_none()
    if not job or job.user_id != user.id:
        raise HTTPException(status_code=404, detail="Job not found")

    pr = await db.execute(
        select(Payment).where(Payment.job_id == body.job_id).order_by(Payment.created_at.desc())
    )
    pay = pr.scalars().first()
    if not pay:
        raise HTTPException(status_code=404, detail="Payment record not found")

    if body.provider == "stripe":
        if not settings.stripe_secret_key:
            raise HTTPException(status_code=503, detail="Stripe not configured")
        stripe.api_key = settings.stripe_secret_key
        intent_id = body.payload.get("payment_intent_id")
        if not intent_id:
            raise HTTPException(status_code=422, detail="payment_intent_id required")
        intent = stripe.PaymentIntent.retrieve(intent_id)
        pay.status = "succeeded" if intent.status == "succeeded" else "failed"
        pay.raw_payload = {**(pay.raw_payload or {}), "verify": body.payload}
        await db.flush()
        return {"ok": pay.status == "succeeded", "status": pay.status}

    if not settings.razorpay_key_id or not settings.razorpay_key_secret:
        raise HTTPException(status_code=503, detail="Razorpay not configured")
    rz = RazorpayClient(auth=(settings.razorpay_key_id, settings.razorpay_key_secret))
    try:
        rz.utility.verify_payment_signature(body.payload)
        pay.status = "succeeded"
    except Exception:
        pay.status = "failed"
    pay.raw_payload = {**(pay.raw_payload or {}), "verify": body.payload}
    await db.flush()
    return {"ok": pay.status == "succeeded", "status": pay.status}
