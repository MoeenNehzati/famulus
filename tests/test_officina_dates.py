from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from officina.common import dates  # noqa: E402
from officina.common.dates import format_date_key, normalize_date_key, parse_date_key  # noqa: E402


def test_format_date_key_is_m_d_yy():
    assert format_date_key(datetime(2026, 7, 2)) == "7-2-26"
    assert format_date_key(date(2007, 1, 5)) == "1-5-07"
    assert format_date_key(datetime(2099, 12, 31)) == "12-31-99"


def test_parse_date_key_reads_m_d_yy():
    assert parse_date_key("7-2-26") == date(2026, 7, 2)
    assert parse_date_key("1-5-07") == date(2007, 1, 5)
    assert parse_date_key("12-31-99") == date(2099, 12, 31)


def test_normalize_date_key_accepts_storage_key_and_iso():
    assert normalize_date_key("07-03-26") == "7-3-26"
    assert normalize_date_key("2026-07-04") == "7-4-26"


def test_get_today_date_key_uses_m_d_yy(monkeypatch):
    class FakeDate(date):
        @classmethod
        def today(cls):
            return cls(2007, 1, 5)

    monkeypatch.setattr(dates, "date", FakeDate)

    assert dates.get_today_date_key() == "1-5-07"
