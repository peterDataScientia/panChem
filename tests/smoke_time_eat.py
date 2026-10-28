"""Verify panChem user-facing dates are East Africa Time."""
from datetime import datetime, timezone

from time_utils import format_eat, EAST_AFRICA

assert format_eat("2026-10-28T00:10:26Z") == "28 Oct 2026 · 03:10 EAT"
assert format_eat("2026-10-27T23:30:00+00:00") == "28 Oct 2026 · 02:30 EAT"
assert format_eat("2026-10-28T08:00:00+05:00") == "28 Oct 2026 · 06:00 EAT"
assert format_eat(None) == "Time unavailable"
assert format_eat("2026-10-28T12:00:00") == "Time unavailable"
assert datetime(2026, 10, 28, tzinfo=timezone.utc).astimezone(EAST_AFRICA).utcoffset().total_seconds() == 10800
print("East Africa Time conversions passed.")
