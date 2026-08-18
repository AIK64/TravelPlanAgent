from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError


def test_trip_day_count(hangzhou_trip):
    assert hangzhou_trip.day_count == 3


def test_transport_anchor_requires_timezone(hangzhou_trip):
    payload = hangzhou_trip.model_dump()
    payload["arrival"]["at"] = datetime(2026, 10, 2, 10, 30)
    with pytest.raises(ValidationError, match="timezone-aware"):
        type(hangzhou_trip).model_validate(payload)

