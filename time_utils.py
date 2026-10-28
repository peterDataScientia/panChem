"""Human-readable timestamps for panChem's East Africa Time interface."""
from datetime import datetime
from zoneinfo import ZoneInfo

EAST_AFRICA = ZoneInfo("Africa/Dar_es_Salaam")


def format_eat(value):
    """Display offset-aware GitHub timestamps in East Africa Time (UTC+3)."""
    if not value:
        return "Time unavailable"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("Timestamp has no timezone")
        return parsed.astimezone(EAST_AFRICA).strftime("%d %b %Y · %H:%M EAT")
    except (TypeError, AttributeError, ValueError):
        return "Time unavailable"
