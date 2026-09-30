"""
Reusable server-side pagination helper.

Every list endpoint in the app should accept `page` (1-based) and `page_size` query
params and return a PaginatedResponse. Keeping this in one place means every page in
the UI gets identical pagination semantics (page, page_size, total, total_pages) with
no per-endpoint drift.
"""
import math
from typing import Generic, List, Optional, TypeVar, Annotated
from pydantic import BaseModel, BeforeValidator, PlainSerializer
from sqlalchemy.orm import Query

T = TypeVar("T")

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 500


def _coerce_int(v):
    """Runs at VALIDATION time (model construction / from_attributes). Turns a stray
    float (NaN, Infinity, or a whole-number float like 2.0) into a clean int/None."""
    if v is None or isinstance(v, bool):
        return v
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return None
        return int(round(v))
    return v


def _serialize_int(v):
    """Runs at SERIALIZATION time (response dump). Belt-and-suspenders alongside
    _coerce_int: pydantic-core's int serializer raises
    "TypeError: 'float' object cannot be interpreted as an integer" whenever the value
    actually stored on the model instance is still a raw float when dump_python()/
    dump_json() runs — which validation-time coercion alone does not always catch
    (e.g. instances built via from_attributes off a fresh ORM object, or any path that
    doesn't route the value through the field's BeforeValidator first). A
    PlainSerializer intercepts unconditionally at dump time, so this class of crash
    can no longer happen regardless of how the float got onto the instance."""
    if v is None or isinstance(v, bool):
        return v
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return None
        return int(round(v))
    return v


SafeInt = Annotated[
    int,
    BeforeValidator(_coerce_int),
    PlainSerializer(_serialize_int, return_type=Optional[int], when_used="always"),
]
SafeOptionalInt = Annotated[
    Optional[int],
    BeforeValidator(_coerce_int),
    PlainSerializer(_serialize_int, return_type=Optional[int], when_used="always"),
]


class PaginatedResponse(BaseModel, Generic[T]):
    items: List[T]
    total: SafeInt
    page: SafeInt
    page_size: SafeInt
    total_pages: SafeInt


def paginate_query(query: Query, page: int = 1, page_size: int = DEFAULT_PAGE_SIZE):
    """Applies offset/limit to a SQLAlchemy Query and returns
    (items, total, page, page_size, total_pages). `total` is computed with a COUNT
    query rather than len(all()) so it stays cheap on large tables."""
    page = max(1, page or 1)
    page_size = min(max(1, page_size or DEFAULT_PAGE_SIZE), MAX_PAGE_SIZE)

    total = query.order_by(None).count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    total_pages = (total + page_size - 1) // page_size if page_size else 0

    return items, total, page, page_size, total_pages


def paginate_list(items: List[T], page: int = 1, page_size: int = DEFAULT_PAGE_SIZE):
    """Same contract as paginate_query but for an already-materialized Python list
    (used where filtering happens in-memory, e.g. searching JSON row_data)."""
    page = max(1, page or 1)
    page_size = min(max(1, page_size or DEFAULT_PAGE_SIZE), MAX_PAGE_SIZE)

    total = len(items)
    start = (page - 1) * page_size
    page_items = items[start:start + page_size]
    total_pages = (total + page_size - 1) // page_size if page_size else 0

    return page_items, total, page, page_size, total_pages