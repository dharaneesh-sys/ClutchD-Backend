from pydantic import BaseModel, Field


class FAQResponse(BaseModel):
    id: str
    question: str
    answer: str
    category: str
    order: int


class FAQListResponse(BaseModel):
    faqs: list[FAQResponse]
