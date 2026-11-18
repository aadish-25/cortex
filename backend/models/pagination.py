from typing import Generic, TypeVar
from pydantic import BaseModel, ConfigDict

T = TypeVar("T")


class PaginatedResponse(BaseModel, Generic[T]):
    """Consistent offset-pagination envelope used by list endpoints."""

    model_config = ConfigDict(from_attributes=True)

    items: list[T]
    page: int
    page_size: int
    total: int
    total_pages: int
