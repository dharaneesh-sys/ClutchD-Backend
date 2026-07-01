from pydantic import BaseModel, Field


class UserSettingsUpdate(BaseModel):
    push_notifications: bool | None = None
    sms_notifications: bool | None = None
    email_notifications: bool | None = None
    theme: str | None = Field(None, max_length=16)
    language: str | None = Field(None, max_length=8)


class UserSettingsResponse(BaseModel):
    push_notifications: bool
    sms_notifications: bool
    email_notifications: bool
    theme: str
    language: str


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=6, max_length=128)
