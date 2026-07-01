from pydantic import BaseModel, Field


class SafetyContentResponse(BaseModel):
    title: str
    content: str
