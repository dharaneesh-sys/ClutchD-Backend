"""Partner billing API: ledger, rent invoices, incentives, onboarding.

Providers only ever see their own rows (a foreign invoice id is a 404).
Rent is garage-only. Accounting records only — no payouts.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession, require_admin
from app.models.billing import IncentiveRule, ProviderLedgerEntry, RentInvoice
from app.models.garage import Garage
from app.models.mechanic import Mechanic
from app.models.user import User
from app.schemas import billing as s
from app.services import billing_service as svc

router = APIRouter(tags=["billing"])
AdminUser = Annotated[User, Depends(require_admin)]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0)]


def _http(exc: Exception) -> HTTPException:
    if isinstance(exc, LookupError):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    msg = str(exc)
    code = status.HTTP_409_CONFLICT if "already" in msg.lower() or "cannot" in msg.lower() else 422
    return HTTPException(code, msg)


async def _provider(db, user: User) -> tuple[str, uuid.UUID]:
    if user.role == "garage":
        r = await db.execute(select(Garage.id).where(Garage.user_id == user.id))
        kind = "garage"
    elif user.role == "mechanic":
        r = await db.execute(select(Mechanic.id).where(Mechanic.user_id == user.id))
        kind = "mechanic"
    else:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Partners only")
    pid = r.scalar_one_or_none()
    if pid is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Provider profile not found")
    return kind, pid


async def _garage_id(db, user: User) -> uuid.UUID:
    kind, pid = await _provider(db, user)
    if kind != "garage":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Rent applies to garages only")
    return pid


# ── Provider ─────────────────────────────────────────────────────────────


@router.get("/providers/ledger", response_model=s.LedgerPage)
async def my_ledger(db: DbSession, user: CurrentUser, limit: Limit = 50, offset: Offset = 0):
    kind, pid = await _provider(db, user)
    rows, bal = await svc.ledger_for(db, kind, pid, limit=limit, offset=offset)
    return s.LedgerPage(items=rows, balance_paise=bal, limit=limit, offset=offset)


@router.get("/providers/rent/invoices", response_model=s.RentInvoicePage)
async def my_invoices(db: DbSession, user: CurrentUser, limit: Limit = 20, offset: Offset = 0):
    gid = await _garage_id(db, user)
    where = RentInvoice.garage_id == gid
    total = (await db.execute(select(func.count()).select_from(RentInvoice).where(where))).scalar() or 0
    rows = (
        await db.execute(select(RentInvoice).where(where).order_by(RentInvoice.period_start.desc()).offset(offset).limit(limit))
    ).scalars().all()
    return s.RentInvoicePage(items=rows, total=total, limit=limit, offset=offset)


@router.post("/providers/rent/invoices/{invoice_id}/pay", response_model=s.RentInvoiceOut)
async def pay_invoice(invoice_id: uuid.UUID, body: s.RentPayIn, db: DbSession, user: CurrentUser):
    gid = await _garage_id(db, user)
    try:
        return await svc.submit_rent_reference(db, gid, invoice_id, body.reference.strip())
    except (svc.BillingError, LookupError) as exc:
        raise _http(exc)


@router.get("/providers/incentives", response_model=s.IncentiveSummary)
async def my_incentives(db: DbSession, user: CurrentUser):
    kind, pid = await _provider(db, user)
    rate = await svc.effective_rate_bps(db, kind, pid)
    where = (
        ProviderLedgerEntry.provider_type == kind,
        ProviderLedgerEntry.provider_id == pid,
        ProviderLedgerEntry.kind == "incentive",
    )
    earned = (await db.execute(select(func.coalesce(func.sum(ProviderLedgerEntry.amount_paise), 0)).where(*where))).scalar()
    recent = (
        await db.execute(select(ProviderLedgerEntry).where(*where).order_by(ProviderLedgerEntry.effective_at.desc()).limit(10))
    ).scalars().all()
    return s.IncentiveSummary(rate_bps=rate, earned_paise=int(earned or 0), recent=recent)


@router.get("/providers/onboarding", response_model=s.OnboardingOut)
async def my_onboarding(db: DbSession, user: CurrentUser):
    kind, pid = await _provider(db, user)
    return await svc.get_onboarding(db, kind, pid)


@router.patch("/providers/onboarding", response_model=s.OnboardingOut)
async def set_my_onboarding(body: s.OnboardingIn, db: DbSession, user: CurrentUser):
    kind, pid = await _provider(db, user)
    ob = await svc.get_onboarding(db, kind, pid)
    try:
        return await svc.set_onboarding_step(db, ob, body.step)
    except svc.BillingError as exc:
        raise _http(exc)


# ── Admin ────────────────────────────────────────────────────────────────


@router.get("/admin/rent/config", response_model=s.RentConfigOut)
async def get_rent_config(db: DbSession, admin: AdminUser):
    return await svc.get_config(db)


@router.put("/admin/rent/config", response_model=s.RentConfigOut)
async def put_rent_config(body: s.RentConfigIn, db: DbSession, admin: AdminUser):
    try:
        return await svc.update_config(
            db, rent_amount_paise=body.rent_amount_paise, rent_due_day=body.rent_due_day, admin_id=admin.id
        )
    except svc.BillingError as exc:
        raise _http(exc)


@router.get("/admin/rent/invoices", response_model=s.RentInvoicePage)
async def admin_invoices(
    db: DbSession,
    admin: AdminUser,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    limit: Limit = 20,
    offset: Offset = 0,
):
    q = select(RentInvoice)
    cq = select(func.count()).select_from(RentInvoice)
    if status_filter:
        q, cq = q.where(RentInvoice.status == status_filter), cq.where(RentInvoice.status == status_filter)
    total = (await db.execute(cq)).scalar() or 0
    rows = (await db.execute(q.order_by(RentInvoice.created_at.desc()).offset(offset).limit(limit))).scalars().all()
    return s.RentInvoicePage(items=rows, total=total, limit=limit, offset=offset)


@router.post("/admin/rent/invoices", response_model=s.RentInvoiceOut, status_code=status.HTTP_201_CREATED)
async def admin_issue(body: s.IssueRentIn, db: DbSession, admin: AdminUser):
    garage = await db.get(Garage, body.garage_id)
    if garage is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Garage not found")
    start, end = svc.month_period(body.period_start or svc.today_local())
    try:
        inv, _created = await svc.issue_rent_invoice(db, garage, start, end)
    except svc.BillingError as exc:
        raise _http(exc)
    return inv


@router.post("/admin/rent/invoices/{invoice_id}/confirm", response_model=s.RentInvoiceOut)
async def admin_confirm(invoice_id: uuid.UUID, db: DbSession, admin: AdminUser):
    try:
        return await svc.confirm_rent_payment(db, invoice_id, admin.id)
    except (svc.BillingError, LookupError) as exc:
        raise _http(exc)


@router.post("/admin/rent/invoices/{invoice_id}/waive", response_model=s.RentInvoiceOut)
async def admin_waive(invoice_id: uuid.UUID, body: s.WaiveIn, db: DbSession, admin: AdminUser):
    try:
        return await svc.waive_rent(db, invoice_id, admin.id, body.reason.strip())
    except (svc.BillingError, LookupError) as exc:
        raise _http(exc)


@router.get("/admin/incentives/rules", response_model=list[s.IncentiveRuleOut])
async def admin_rules(db: DbSession, admin: AdminUser):
    rows = await db.execute(select(IncentiveRule).where(IncentiveRule.active.is_(True)).order_by(IncentiveRule.created_at.desc()))
    return rows.scalars().all()


@router.put("/admin/incentives/rules", response_model=s.IncentiveRuleOut)
async def admin_set_rule(body: s.IncentiveRuleIn, db: DbSession, admin: AdminUser):
    try:
        return await svc.set_incentive_rule(
            db,
            rate_bps=body.rate_bps,
            admin_id=admin.id,
            scope=body.scope,
            garage_id=body.garage_id,
            mechanic_id=body.mechanic_id,
        )
    except svc.BillingError as exc:
        raise _http(exc)
