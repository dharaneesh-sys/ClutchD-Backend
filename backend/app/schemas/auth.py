from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)


class CustomerRegister(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)
    confirmPassword: str | None = Field(None, max_length=128)


class MechanicRegister(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)
    confirmPassword: str | None = Field(None, max_length=128)
    fullName: str = Field(min_length=2, max_length=100)
    phone: str = Field(min_length=10, max_length=15)
    experience: str = Field(min_length=1, max_length=50)
    expertise: list[str] = Field(default_factory=list, max_length=20)
    location: str = Field(min_length=2, max_length=500)
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)


class GarageRegister(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)
    confirmPassword: str | None = Field(None, max_length=128)
    garageName: str = Field(min_length=2, max_length=200)
    ownerName: str = Field(min_length=2, max_length=100)
    phone: str = Field(min_length=10, max_length=15)
    location: str = Field(min_length=2, max_length=500)
    services: list[str] = Field(default_factory=list, max_length=50)
    mechanicCount: str = Field(min_length=1, max_length=5)
    operatingHours: str = Field(min_length=1, max_length=50)
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)


class SignupPayload(BaseModel):
    """Unified signup body used by the frontend."""
    role: str = Field(pattern="^(customer|mechanic|garage)$")
    email: EmailStr | None = None
    password: str | None = Field(None, max_length=128)
    confirmPassword: str | None = Field(None, max_length=128)
    fullName: str | None = Field(None, max_length=100)
    phone: str | None = Field(None, max_length=15)
    experience: str | None = Field(None, max_length=50)
    expertise: list[str] | None = Field(None, max_length=20)
    location: str | None = Field(None, max_length=500)
    garageName: str | None = Field(None, max_length=200)
    ownerName: str | None = Field(None, max_length=100)
    services: list[str] | None = Field(None, max_length=50)
    mechanicCount: str | None = Field(None, max_length=5)
    operatingHours: str | None = Field(None, max_length=50)
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)


class GoogleOAuthRequest(BaseModel):
    credential: str = Field(max_length=8192)
    role: str | None = Field(None, pattern="^(customer|mechanic|garage)$")
    state: str | None = Field(None, min_length=8, max_length=128)


class TokenResponse(BaseModel):
    token: str
    user: dict


class MessageResponse(BaseModel):
    message: str


class UserProfile(BaseModel):
    id: str
    email: str
    role: str
    name: str | None = None
    phone: str | None = None
    experience: str | None = None
    expertise: list[str] | None = None
    location: str | None = None
    rating: float | None = None
    isOnline: bool | None = None
    ownerName: str | None = None
    services: list[str] | None = None
    mechanicCount: int | None = None
    operatingHours: str | None = None


class ProfileUpdateRequest(BaseModel):
    """Update mechanic or garage profile fields."""
    fullName: str | None = Field(None, max_length=100)
    phone: str | None = Field(None, max_length=15)
    location: str | None = Field(None, max_length=500)
    expertise: list[str] | None = Field(None, max_length=20)
    services: list[str] | None = Field(None, max_length=50)
    garageName: str | None = Field(None, max_length=200)
    ownerName: str | None = Field(None, max_length=100)
    operatingHours: str | None = Field(None, max_length=50)
    mechanicCount: int | None = Field(None, ge=0, le=999)
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class PasswordResetRequest(BaseModel):
    email: EmailStr
    code: str = Field(min_length=8, max_length=16)
    newPassword: str = Field(min_length=6, max_length=128)
