from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class DataResponse(BaseModel, Generic[T]):
    message: str = "ok"
    data: T


class DataMessageResponse(BaseModel, Generic[T]):
    message: str
    data: T | None = None


class PaginatedResponse(BaseModel, Generic[T]):
    message: str = "ok"
    data: list[T]
    total: int
    page: int
    page_size: int


def ok(data, *, message="ok"):
    return {"message": message, "data": data}


def paginated(items, *, total, page, page_size, message="ok"):
    return {
        "message": message,
        "data": list(items),
        "total": total,
        "page": page,
        "page_size": page_size,
    }
