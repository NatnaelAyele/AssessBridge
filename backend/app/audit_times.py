"""UTC serialization and chronological comparison for server audit records."""

from datetime import datetime, timezone


def utc(value: datetime | str) -> datetime:
    """Database-naive values are UTC; normalize explicit offsets as well."""
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def iso(value: datetime | None) -> str | None:
    return utc(value).isoformat().replace("+00:00", "Z") if value is not None else None
