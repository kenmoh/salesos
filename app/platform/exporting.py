"""Small helpers shared by the platform management and analytics surfaces."""

from __future__ import annotations

import csv
import io
from datetime import date, datetime
from typing import Any, Iterable, Sequence

from fastapi import HTTPException
from fastapi.responses import Response


def parse_iso_date(value: str | None, field: str) -> date | None:
    """Parse a ``YYYY-MM-DD`` query parameter, raising a clean 422."""
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"{field} must be YYYY-MM-DD")


def as_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def csv_response(
    filename: str,
    columns: Sequence[str],
    rows: Iterable[dict[str, Any]],
) -> Response:
    """Render rows as a downloadable CSV response."""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(columns), extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: _cell(row.get(key)) for key in columns})
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def csv_response_from_pairs(
    filename: str,
    header: Sequence[str],
    rows: Iterable[Sequence[Any]],
) -> Response:
    """Render positional rows (already ordered) as CSV."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(list(header))
    for row in rows:
        writer.writerow([_cell(value) for value in row])
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
