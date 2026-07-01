from uuid import UUID

from pydantic import BaseModel


class FavoriteCreate(BaseModel):
    product_id: str


class FavoriteResponse(BaseModel):
    id: str
    product_id: str
    created_at: str | None = None

    class ProductInfo(BaseModel):
        id: str
        name: str
        price: float
        image_url: str | None = None
        rating: float | None = None
        category_name: str | None = None

    product: ProductInfo | None = None


class FavoriteListResponse(BaseModel):
    favorites: list[FavoriteResponse]
    total: int
