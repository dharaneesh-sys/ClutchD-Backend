import hashlib
import hmac
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import select, func

from app.api.deps import CurrentUser, DbSession
from app.core.config import get_settings
from app.core.limiter import limiter
from app.models.enums import UserRole
from app.models.job import Job
from app.models.payment import Payment

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/payments", tags=["payments"])


def _get_razorpay_client():
    settings = get_settings()
    if not settings.razorpay_key_id or not settings.razorpay_key_secret:
        logger.warning("Using MOCK Razorpay client because keys are missing in environment")
        
        class MockQrcode:
            def create(self, kwargs):
                import time
                amt = kwargs.get("payment_amount", 0) / 100
                return {
                    "id": f"qr_mock_{int(time.time())}",
                    # Use a real QR generation API to show a dummy UPI link
                    "image_url": f"https://api.qrserver.com/v1/create-qr-code/?size=250x250&data=upi://pay?pa=mock@upi%26pn=ClutchD%26am={amt}%26cu=INR",
                    "close_by": kwargs.get("close_by"),
                    "status": "active"
                }

            def fetch(self, qr_id):
                import time
                try:
                    ts = int(qr_id.split('_')[-1])
                except:
                    ts = time.time()
                now = time.time()
                # Simulate payment success after 8 seconds
                paid = (now - ts) > 8
                return {
                    "status": "closed" if paid else "active",
                    "payments_count_received": 1 if paid else 0
                }

        class MockOrder:
            def create(self, kwargs):
                import time
                return {
                    "id": f"order_mock_{int(time.time())}",
                    "amount": kwargs.get("amount"),
                    "currency": kwargs.get("currency"),
                }

        class MockClient:
            def __init__(self):
                self.qrcode = MockQrcode()
                self.order = MockOrder()

        return MockClient()

    import razorpay
    return razorpay.Client(auth=(settings.razorpay_key_id, settings.razorpay_key_secret))


# ── Create Order ──────────────────────────────────────
class PaymentCreateRequest(BaseModel):
    job_id: UUID
    amount: int  # in paise (100 = ₹1)
    currency: str = "inr"


@router.post("/create")
@limiter.limit("5/minute")
async def payment_create(request: Request, body: PaymentCreateRequest, db: DbSession, user: CurrentUser):
    if body.amount < 100 or body.amount > 50000000:
        raise HTTPException(status_code=422, detail="Amount must be between ₹1 and ₹5,00,000")

    # Verify job belongs to user
    jr = await db.execute(select(Job).where(Job.id == body.job_id))
    job = jr.scalar_one_or_none()
    if not job or job.user_id != user.id:
        raise HTTPException(status_code=404, detail="Job not found")

    rz = _get_razorpay_client()
    try:
        order = rz.order.create({
            "amount": body.amount,
            "currency": body.currency.upper(),
            "receipt": f"job_{job.id}",
            "payment_capture": 1,
        })
    except Exception as e:
        logger.error("Razorpay order creation failed: %s", e)
        raise HTTPException(status_code=502, detail="Payment gateway error")

    # Record pending payment
    pmt = Payment(
        job_id=job.id,
        user_id=user.id,
        amount=body.amount,
        currency=body.currency.lower(),
        provider="razorpay",
        provider_order_id=order["id"],
        status="pending",
    )
    db.add(pmt)
    await db.flush()

    settings = get_settings()
    return {
        "order_id": order["id"],
        "amount": body.amount,
        "currency": body.currency.upper(),
        "key_id": settings.razorpay_key_id,
        "payment_id": str(pmt.id),
    }


# ── Verify Payment (client-side callback) ─────────────
class PaymentVerifyRequest(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str
    payment_id: UUID


@router.post("/verify")
async def payment_verify(body: PaymentVerifyRequest, db: DbSession, user: CurrentUser):
    settings = get_settings()
    if not settings.razorpay_key_secret:
        raise HTTPException(status_code=503, detail="Payment gateway not configured")

    # Verify signature
    message = f"{body.razorpay_order_id}|{body.razorpay_payment_id}"
    expected_sig = hmac.new(
        settings.razorpay_key_secret.encode(),
        message.encode(),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected_sig, body.razorpay_signature):
        raise HTTPException(status_code=400, detail="Payment verification failed")

    # Update payment record
    pr = await db.execute(select(Payment).where(
        Payment.id == body.payment_id,
        Payment.user_id == user.id,
    ))
    pmt = pr.scalar_one_or_none()
    if not pmt:
        raise HTTPException(status_code=404, detail="Payment record not found")

    # Idempotent — don't re-process
    if pmt.status == "captured":
        return {"ok": True, "status": "already_captured"}

    pmt.status = "captured"
    pmt.provider_payment_id = body.razorpay_payment_id
    pmt.method = "online"

    # Update job status
    jr = await db.execute(select(Job).where(Job.id == pmt.job_id))
    job = jr.scalar_one_or_none()
    if job and job.status != "completed":
        job.status = "completed"
        
    await db.flush()
    
    # Trigger payout logic
    if job:
        from app.services.payout_service import process_payouts
        await process_payouts(db, pmt, job)
        
    return {"ok": True, "status": "captured"}


# ── Webhook (server-to-server from Razorpay) ──────────
@router.post("/webhook")
async def payment_webhook(request: Request, db: DbSession):
    settings = get_settings()
    if not settings.razorpay_webhook_secret:
        raise HTTPException(status_code=503, detail="Webhook not configured")

    # Get raw body for signature verification
    raw_body = await request.body()
    sig_header = request.headers.get("X-Razorpay-Signature", "")

    expected = hmac.new(
        settings.razorpay_webhook_secret.encode(),
        raw_body,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, sig_header):
        raise HTTPException(status_code=400, detail="Invalid webhook signature")

    import json
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event = payload.get("event", "")
    entity = payload.get("payload", {}).get("payment", {}).get("entity", {})
    order_id = entity.get("order_id")

    if not order_id:
        return {"ok": True}  # Event type we don't handle

    # Find payment by provider order ID
    pr = await db.execute(select(Payment).where(Payment.provider_order_id == order_id))
    pmt = pr.scalar_one_or_none()
    if not pmt:
        logger.warning("Webhook: no payment found for order %s", order_id)
        return {"ok": True}

    # Idempotent processing
    if event == "payment.captured" and pmt.status != "captured":
        pmt.status = "captured"
        pmt.provider_payment_id = entity.get("id")
        pmt.method = entity.get("method", "online")

        jr = await db.execute(select(Job).where(Job.id == pmt.job_id))
        job = jr.scalar_one_or_none()
        if job and job.status != "completed":
            job.status = "completed"
        await db.flush()
        
        # Trigger payout logic
        if job:
            from app.services.payout_service import process_payouts
            await process_payouts(db, pmt, job)

    elif event == "payment.failed" and pmt.status == "pending":
        pmt.status = "failed"
        await db.flush()

    return {"ok": True}


# ── QR Code Payment ───────────────────────────────────
class QRCreateRequest(BaseModel):
    job_id: UUID
    amount: int


@router.post("/qr")
@limiter.limit("5/minute")
async def create_qr(request: Request, body: QRCreateRequest, db: DbSession, user: CurrentUser):
    if body.amount < 100 or body.amount > 50000000:
        raise HTTPException(status_code=422, detail="Amount must be between ₹1 and ₹5,00,000")

    # Verify job belongs to user
    jr = await db.execute(select(Job).where(Job.id == body.job_id, Job.user_id == user.id))
    job = jr.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    rz = _get_razorpay_client()
    try:
        qr = rz.qrcode.create({
            "type": "upi_qr",
            "name": f"ClutchD Payment #{str(job.id)[:8]}",
            "usage": "single_use",
            "fixed_amount": True,
            "payment_amount": body.amount,
            "description": f"Payment for service job",
            "close_by": int(__import__("time").time()) + 900,  # 15 min expiry
        })
    except Exception as e:
        logger.error("QR creation failed: %s", e)
        raise HTTPException(status_code=502, detail="QR code generation failed")

    # Record pending payment
    pmt = Payment(
        job_id=job.id,
        user_id=user.id,
        amount=body.amount,
        currency="inr",
        provider="razorpay",
        provider_order_id=qr.get("id", ""),
        status="pending",
        method="qr",
    )
    db.add(pmt)
    await db.flush()

    return {
        "qr_id": qr.get("id"),
        "image_url": qr.get("image_url"),
        "amount": body.amount,
        "expires_at": qr.get("close_by"),
        "payment_id": str(pmt.id),
    }


@router.get("/qr/{qr_id}/status")
async def qr_status(qr_id: str, db: DbSession, user: CurrentUser):
    rz = _get_razorpay_client()
    try:
        qr = rz.qrcode.fetch(qr_id)
    except Exception:
        raise HTTPException(status_code=404, detail="QR code not found")

    paid = qr.get("status") == "closed" and qr.get("payments_count_received", 0) > 0
    return {
        "status": qr.get("status", "active"),
        "paid": paid,
    }


# ── Cash Payment ──────────────────────────────────────
class CashPaymentRequest(BaseModel):
    job_id: UUID
    amount: int


@router.post("/cash")
async def cash_payment(body: CashPaymentRequest, db: DbSession, user: CurrentUser):
    """Mark a job as paid via cash. Only the assigned mechanic can do this."""
    if body.amount < 100 or body.amount > 50000000:
        raise HTTPException(status_code=422, detail="Invalid amount")

    jr = await db.execute(select(Job).where(Job.id == body.job_id))
    job = jr.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Verify user is the assigned mechanic or admin
    from app.models.mechanic import Mechanic
    if user.role == UserRole.mechanic.value:
        mr = await db.execute(select(Mechanic).where(Mechanic.user_id == user.id))
        mech = mr.scalar_one_or_none()
        if not mech or job.assigned_mechanic_id != mech.id:
            raise HTTPException(status_code=403, detail="Not assigned to this job")
    elif user.role != UserRole.admin.value:
        raise HTTPException(status_code=403, detail="Not authorized")

    pmt = Payment(
        job_id=job.id,
        user_id=job.user_id,  # customer's payment
        amount=body.amount,
        currency="inr",
        provider="manual",
        status="captured",
        method="cash",
    )
    db.add(pmt)
    job.status = "completed"
    await db.flush()

    return {"ok": True, "payment_id": str(pmt.id)}


# ── Payment History ───────────────────────────────────
@router.get("/history")
async def payment_history(
    db: DbSession,
    user: CurrentUser,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
):
    q = select(Payment).where(Payment.user_id == user.id).order_by(Payment.created_at.desc())
    total_r = await db.execute(select(func.count(Payment.id)).where(Payment.user_id == user.id))
    total = total_r.scalar() or 0

    r = await db.execute(q.offset(skip).limit(limit))
    payments = r.scalars().all()

    return {
        "payments": [
            {
                "id": str(p.id),
                "job_id": str(p.job_id),
                "amount": p.amount,
                "currency": getattr(p, "currency", "inr"),
                "provider": p.provider,
                "status": p.status,
                "method": getattr(p, "method", None),
                "created_at": p.created_at.isoformat() if p.created_at else None,
            }
            for p in payments
        ],
        "total": total,
    }
