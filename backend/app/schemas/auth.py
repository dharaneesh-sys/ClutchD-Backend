from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, model_validator


class TokenResponse(BaseModel):
    token: str
    user: dict[str, Any]


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6)


class CustomerRegister(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6)
    confirmPassword: str | None = None


class MechanicRegister(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6)
    confirmPassword: str | None = None
    fullName: str = Field(min_length=2)
    phone: str = Field(min_length=10)
    experience: str = Field(min_length=1)
    expertise: list[str] = Field(min_length=1)
    location: str = Field(min_length=2)
    latitude: float | None = None
    longitude: float | None = None


class GarageRegister(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6)
    confirmPassword: str | None = None
    garageName: str = Field(min_length=2)
    ownerName: str = Field(min_length=2)
    phone: str = Field(min_length=10)
    location: str = Field(min_length=2)
    services: list[str] = Field(min_length=1)
    mechanicCount: str = Field(min_length=1)
    operatingHours: str = Field(min_length=1)
    latitude: float | None = None
    longitude: float | None = None


class SignupPayload(BaseModel):
    """Frontend sends role + one of the role-specific shapes."""

    role: Literal["customer", "mechanic", "garage"]
    email: EmailStr | None = None
    password: str | None = None
    confirmPassword: str | None = None
    fullName: str | None = None
    phone: str | None = None
    experience: str | None = None
    expertise: list[str] | None = None
    location: str | None = None
    garageName: str | None = None
    ownerName: str | None = None
    services: list[str] | None = None
    mechanicCount: str | None = None
    operatingHours: str | None = None
    latitude: float | None = None
    longitude: float | None = None

    @model_validator(mode="after")
    def validate_role_fields(self):
        if self.role == "customer":
            if not self.email or not self.password:
                raise ValueError("email and password required")
        elif self.role == "mechanic":
            for f in ("email", "password", "fullName", "phone", "experience", "expertise", "location"):
                if getattr(self, f) in (None, [], ""):
                    raise ValueError(f"{f} required for mechanic")
            if not self.expertise:
                raise ValueError("expertise required")
        elif self.role == "garage":
            for f in ("email", "password", "garageName", "ownerName", "phone", "location", "services", "mechanicCount", "operatingHours"):
                if getattr(self, f) in (None, [], ""):
                    raise ValueError(f"{f} required for garage")
        return self


class GoogleOAuthRequest(BaseModel):
    id_token: str | None = Field(default=None, alias="credential")
    token: str | None = None
    role: Literal["customer", "mechanic", "garage"] | None = None
    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def pick_token(self):
        t = self.id_token or self.token
        if not t:
            raise ValueError("id_token or credential required")
        self.id_token = t
        return self


class MessageResponse(BaseModel):
    message: str
