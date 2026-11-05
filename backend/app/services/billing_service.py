"""Partner billing: incentives on captured jobs, monthly garage rent, ledger,
and onboarding progress.

Rules (plan decisions):
- Money is integer paise. ``Job.service_amount`` is Float rupees and is
  converted with Decimal before applying basis points.
- One incentive per completed + captured job (``incentive:{job_id}``),
  default 5% of service_amount; cancelled/unpaid jobs earn nothing.
- Rent: one invoice per garage per calendar month (Asia/Kolkata), default
  ₹499, due on the configured day. No auto-debit: a provider reference puts
  the invoice in ``payment_pending``; only an admin confirm marks it paid.
  A waiver needs a reason.
- The ledger is accounting only: no payout/settlement or job-earning rows.
- Everything is inert unless ``settings.billing_enabled`` is true.
"""

from __future__ import annotations

import calendar
import uuid
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.billing import (
    DEFAULT_INCENTIVE_BPS,
    DEFAULT_RENT_PAISE,
    ONBOARDING_STEPS,
    BillingConfig,
    IncentiveRule,
    ProviderLedgerEntry,
    ProviderOnboarding,
    RentInvoice,
)
from app.models.garage import Garage
from app.models.job import Job
from app.models.payment import Payment


class BillingError(ValueError):
    """Invalid billing operation (maps to HTTP 400/409/422)."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── Money ────────────────────────────────────────────────────────────────


def rupees_to_paise(rupees: float | int | str) -> int:
    return int((Decimal(str(rupees)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def incentive_paise(service_amount_rupees: float | int | str, rate_bps: int) -> int:
    if rate_bps <= 0 or rate_bps > 10000:
        raise BillingError("rate_bps must be between 1 and 10000")
    amount = Decimal(str(service_amount_rupees)) * 100 * rate_bps / 10000
    return int(amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


# ── Config ───────────────────────────────────────────────────────────────


async def get_config(db: AsyncSession) -> BillingConfig:
    cfg = await db.get(BillingConfig, 1)
    if cfg is None:
        cfg = BillingConfig(id=1, rent_amount_paise=DEFAULT_RENT_PAISE, rent_due_day=1)
        db.add(cfg)
        await db.flush()
    return cfg


async def update_config(
    db: AsyncSession, *, rent_amount_paise: int | None, rent_due_day: int | None, admin_id: uuid.UUID
) -> BillingConfig:
    cfg = await get_config(db)
    if rent_amount_paise is not None:
        if rent_amount_paise <= 0:
            raise BillingError("Rent must be greater than zero")
        cfg.rent_amount_paise = rent_amount_paise
    if rent_due_day is not None:
        if not 1 <= rent_due_day <= 28:
            raise BillingError("Due day must be between 1 and 28")
        cfg.rent_due_day = rent_due_day
    cfg.updated_by = admin_id
    await db.flush()
    return cfg


# ── Ledger ───────────────────────────────────────────────────────────────


async def _post(db: AsyncSession, **fields) -> ProviderLedgerEntry | None:
    """Insert a ledger row; return None if its idempotency key already exists."""
    key = fields["idempotency_key"]
    existing = await db.execute(select(ProviderLedgerEntry).where(ProviderLedgerEntry.idempotency_key == key))
    if existing.scalar_one_or_none():
        return None
    entry = ProviderLedgerEntry(**fields)
    try:
        async with db.begin_nested():
            db.add(entry)
            await db.flush()
    except IntegrityError:
        return None  # concurrent duplicate
    return entry


async def ledger_for(
    db: AsyncSession, provider_type: str, provider_id: uuid.UUID, *, limit: int = 50, offset: int = 0
) -> tuple[list[ProviderLedgerEntry], int]:
    q = (
        select(ProviderLedgerEntry)
        .where(ProviderLedgerEntry.provider_type == provider_type, ProviderLedgerEntry.provider_id == provider_id)
        .order_by(ProviderLedgerEntry.effective_at.desc())
        .offset(offset)
        .limit(limit)
    )
    rows = list((await db.execute(q)).scalars().all())
    bal = await db.execute(
        select(func.coalesce(func.sum(ProviderLedgerEntry.amount_paise), 0)).where(
            ProviderLedgerEntry.provider_type == provider_type, ProviderLedgerEntry.provider_id == provider_id
        )
    )
    return rows, int(bal.scalar() or 0)


# ── Incentives ───────────────────────────────────────────────────────────


async def effective_rate_bps(db: AsyncSession, provider_type: str, provider_id: uuid.UUID) -> int:
    """Most specific active rule wins (provider > global); default 5%."""
    col = IncentiveRule.mechanic_id if provider_type == "mechanic" else IncentiveRule.garage_id
    for cond in ((IncentiveRule.scope == provider_type, col == provider_id), (IncentiveRule.scope == "global",)):
        r = await db.execute(
            select(IncentiveRule)
            .where(IncentiveRule.active.is_(True), *cond)
            .order_by(IncentiveRule.effective_from.desc())
            .limit(1)
        )
        rule = r.scalar_one_or_none()
        if rule:
            return rule.rate_bps
    return DEFAULT_INCENTIVE_BPS


async def record_capture(db: AsyncSession, job: Job) -> ProviderLedgerEntry | None:
    """Called after a payment is captured and the job completed (online verify,
    webhook and cash). Creates at most one incentive credit per job."""
    if not get_settings().billing_enabled or job is None:
        return None
    if job.status != "completed" or not job.service_amount or job.service_amount <= 0:
        return None
    paid = await db.execute(select(Payment.id).where(Payment.job_id == job.id, Payment.status == "captured"))
    if paid.first() is None:
        return None
    if job.assigned_mechanic_id:
        ptype, pid = "mechanic", job.assigned_mechanic_id
    elif job.assigned_garage_id:
        ptype, pid = "garage", job.assigned_garage_id
    else:
        return None
    rate = await effective_rate_bps(db, ptype, pid)
    amount = incentive_paise(job.service_amount, rate)
    if amount <= 0:
        return None
    return await _post(
        db,
        provider_type=ptype,
        provider_id=pid,
        job_id=job.id,
        kind="incentive",
        amount_paise=amount,
        note=f"{rate / 100:g}% of service amount",
        idempotency_key=f"incentive:{job.id}",
    )


async def set_incentive_rule(
    db: AsyncSession,
    *,
    rate_bps: int,
    admin_id: uuid.UUID,
    scope: str = "global",
    garage_id: uuid.UUID | None = None,
    mechanic_id: uuid.UUID | None = None,
    effective_from: datetime | None = None,
) -> IncentiveRule:
    if not 0 < rate_bps <= 10000:
        raise BillingError("rate_bps must be between 1 and 10000")
    if scope not in ("global", "garage", "mechanic"):
        raise BillingError("Invalid scope")
    if scope == "garage" and not garage_id or scope == "mechanic" and not mechanic_id:
        raise BillingError("Scoped rules need the provider id")
    # Deactivate the previous rule for the same scope/provider.
    prev = await db.execute(
        select(IncentiveRule).where(
            IncentiveRule.active.is_(True),
            IncentiveRule.scope == scope,
            IncentiveRule.garage_id.is_(garage_id) if garage_id is None else IncentiveRule.garage_id == garage_id,
            IncentiveRule.mechanic_id.is_(mechanic_id) if mechanic_id is None else IncentiveRule.mechanic_id == mechanic_id,
        )
    )
    for rule in prev.scalars():
        rule.active = False
    rule = IncentiveRule(
        scope=scope,
        garage_id=garage_id,
        mechanic_id=mechanic_id,
        rate_bps=rate_bps,
        active=True,
        effective_from=effective_from or _now(),
        created_by=admin_id,
    )
    db.add(rule)
    await db.flush()
    return rule


# ── Rent ─────────────────────────────────────────────────────────────────


def month_period(day: date) -> tuple[date, date]:
    last = calendar.monthrange(day.year, day.month)[1]
    return date(day.year, day.month, 1), date(day.year, day.month, last)


def today_local() -> date:
    return datetime.now(ZoneInfo(get_settings().billing_timezone)).date()


async def issue_rent_invoice(
    db: AsyncSession, garage: Garage, period_start: date, period_end: date
) -> tuple[RentInvoice, bool]:
    """Idempotent: returns (invoice, created)."""
    key = f"rent:{garage.id}:{period_start.isoformat()}:{period_end.isoformat()}"
    existing = (await db.execute(select(RentInvoice).where(RentInvoice.generation_key == key))).scalar_one_or_none()
    if existing:
        return existing, False
    cfg = await get_config(db)
    amount = garage.rent_amount_paise or cfg.rent_amount_paise
    if amount <= 0:
        raise BillingError("Rent must be greater than zero")
    due = date(period_start.year, period_start.month, min(cfg.rent_due_day, 28))
    inv = RentInvoice(
        garage_id=garage.id,
        period_start=period_start,
        period_end=period_end,
        amount_paise=amount,
        due_date=due,
        status="issued",
        generation_key=key,
    )
    try:
        async with db.begin_nested():
            db.add(inv)
            await db.flush()
    except IntegrityError:
        existing = (await db.execute(select(RentInvoice).where(RentInvoice.generation_key == key))).scalar_one()
        return existing, False
    await _post(
        db,
        provider_type="garage",
        provider_id=garage.id,
        rent_invoice_id=inv.id,
        kind="rent_charge",
        amount_paise=-amount,
        note=f"Rent {period_start:%b %Y}",
        idempotency_key=f"rent_charge:{inv.id}",
    )
    return inv, True


async def issue_monthly_rent(db: AsyncSession, on: date | None = None) -> int:
    """Issue this month's invoice for every garage. Returns invoices created."""
    if not get_settings().billing_enabled:
        return 0
    start, end = month_period(on or today_local())
    created = 0
    for garage in (await db.execute(select(Garage))).scalars():
        _, made = await issue_rent_invoice(db, garage, start, end)
        created += int(made)
    return created


async def _invoice(db: AsyncSession, invoice_id: uuid.UUID, garage_id: uuid.UUID | None = None) -> RentInvoice:
    q = select(RentInvoice).where(RentInvoice.id == invoice_id)
    if garage_id is not None:
        q = q.where(RentInvoice.garage_id == garage_id)
    inv = (await db.execute(q)).scalar_one_or_none()
    if inv is None:
        raise LookupError("Invoice not found")
    return inv


async def submit_rent_reference(db: AsyncSession, garage_id: uuid.UUID, invoice_id: uuid.UUID, reference: str) -> RentInvoice:
    """Provider reports a manual/UPI payment. No money moves; admin verifies."""
    inv = await _invoice(db, invoice_id, garage_id)
    if inv.status not in ("issued", "overdue", "payment_pending"):
        raise BillingError(f"Invoice is {inv.status}")
    reference = (reference or "").strip()
    if not 4 <= len(reference) <= 128:
        raise BillingError("Enter the payment reference (4–128 characters)")
    inv.reference = reference
    inv.status = "payment_pending"
    await db.flush()
    return inv


async def confirm_rent_payment(db: AsyncSession, invoice_id: uuid.UUID, admin_id: uuid.UUID) -> RentInvoice:
    inv = await _invoice(db, invoice_id)
    if inv.status == "paid":
        return inv
    if inv.status == "waived":
        raise BillingError("Invoice was waived")
    inv.status = "paid"
    inv.paid_at = inv.confirmed_at = _now()
    inv.confirmed_by = admin_id
    await _post(
        db,
        provider_type="garage",
        provider_id=inv.garage_id,
        rent_invoice_id=inv.id,
        kind="rent_payment",
        amount_paise=inv.amount_paise,
        note=f"Payment confirmed{f' (ref {inv.reference})' if inv.reference else ''}",
        idempotency_key=f"rent_payment:{inv.id}",
    )
    await db.flush()
    return inv


async def waive_rent(db: AsyncSession, invoice_id: uuid.UUID, admin_id: uuid.UUID, reason: str) -> RentInvoice:
    reason = (reason or "").strip()
    if len(reason) < 3:
        raise BillingError("A waiver needs a reason")
    inv = await _invoice(db, invoice_id)
    if inv.status == "waived":
        return inv
    if inv.status == "paid":
        raise BillingError("Invoice is already paid")
    inv.status = "waived"
    inv.waived_at = _now()
    inv.waived_by = admin_id
    inv.waiver_reason = reason
    await _post(
        db,
        provider_type="garage",
        provider_id=inv.garage_id,
        rent_invoice_id=inv.id,
        kind="waiver",
        amount_paise=inv.amount_paise,
        note=reason,
        idempotency_key=f"waiver:{inv.id}",
    )
    await db.flush()
    return inv


async def mark_overdue(db: AsyncSession, on: date | None = None) -> int:
    day = on or today_local()
    rows = await db.execute(select(RentInvoice).where(RentInvoice.status == "issued", RentInvoice.due_date < day))
    n = 0
    for inv in rows.scalars():
        inv.status = "overdue"
        n += 1
    await db.flush()
    return n


# ── Onboarding ───────────────────────────────────────────────────────────


async def get_onboarding(db: AsyncSession, provider_type: str, provider_id: uuid.UUID) -> ProviderOnboarding:
    r = await db.execute(
        select(ProviderOnboarding).where(
            ProviderOnboarding.provider_type == provider_type, ProviderOnboarding.provider_id == provider_id
        )
    )
    ob = r.scalar_one_or_none()
    if ob is None:
        ob = ProviderOnboarding(provider_type=provider_type, provider_id=provider_id, step="profile")
        db.add(ob)
        await db.flush()
    return ob


async def set_onboarding_step(db: AsyncSession, ob: ProviderOnboarding, step: str) -> ProviderOnboarding:
    if step not in ONBOARDING_STEPS:
        raise BillingError("Unknown onboarding step")
    ob.step = step
    ob.completed_at = _now() if step == "done" else None
    await db.flush()
    return ob
