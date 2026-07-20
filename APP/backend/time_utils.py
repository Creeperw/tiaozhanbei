from datetime import UTC, datetime
from zoneinfo import ZoneInfo

BEIJING_TZ = ZoneInfo("Asia/Shanghai")


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def as_beijing(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC).astimezone(BEIJING_TZ)


def beijing_now() -> datetime:
    return datetime.now(BEIJING_TZ).replace(tzinfo=None)
