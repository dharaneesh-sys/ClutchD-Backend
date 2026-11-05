import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class LedgerEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    kind: str
    amount_paise: int
    status: str
    note: str | None = None
    job_id: uuid.UUID | None = None
    rent_invoice_id: uuid.UUID | None = None
    effective_at: datetime


class LedgerPage(BaseModel):
    items: list[LedgerEntryOut]
    balance_paise: int
    limit: int
    offset: int


class RentInvoiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    garage_id: uuid.UUID
    period_start: date
    period_end: date
    amount_paise: int
    due_date: date
    status: str
    reference: str | None = None
    paid_at: datetime | None = None
    waiver_reason: str | None = None


class RentInvoicePage(BaseModel):
    items: list[RentInvoiceOut]
    total: int
    limit: int
    offset: int


class RentPayIn(BaseModel):
    reference: str = Field(min_length=4, max_length=128)


class WaiveIn(BaseModel):
    reason: str = Field(min_length=3, max_length=512)


class RentConfigIn(BaseModel):
    rent_amount_paise: int | None = Field(default=None, gt=0)
    rent_due_day: int | None = Field(default=None, ge=1, le=28)


class RentConfigOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    rent_amount_paise: int
    rent_due_day: int


class IssueRentIn(BaseModel):
    garage_id: uuid.UUID
    period_start: date | None = None


class IncentiveRuleIn(BaseModel):
    rate_bps: int = Field(gt=0, le=10000)
    scope: str = Field(default="global", pattern="^(global|garage|mechanic)$")
    garage_id: uuid.UUID | None = None
    mechanic_id: uuid.UUID | None = None


class IncentiveRuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    scope: str
    garage_id: uuid.UUID | None = None
    mechanic_id: uuid.UUID | None = None
    rate_bps: int
    active: bool


class IncentiveSummary(BaseModel):
    rate_bps: int
    earned_paise: int
    recent: list[LedgerEntryOut]


class OnboardingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    provider_type: str
    provider_id: uuid.UUID
    step: str
    completed_at: datetime | None = None


class OnboardingIn(BaseModel):
    step: str = Field(pattern="^(profile|garage_details|services|location|kyc_review|done)$")
