from pydantic import BaseModel, EmailStr, Field


class ApplyReferralRequest(BaseModel):
    code: str = Field(min_length=1, max_length=32)


class ReferralCodeResponse(BaseModel):
    code: str
    reward_balance: int
    total_referrals: int
    referral_link: str = ""


class ReferralRewardResponse(BaseModel):
    id: str
    referred_email: str | None = None
    amount: int
    status: str
    created_at: str | None = None


class ReferralHistoryResponse(BaseModel):
    rewards: list[ReferralRewardResponse]
    total_earned: int
    pending_amount: int
