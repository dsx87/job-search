"""Characterization tests for date parsers."""
import datetime as dt

# --- modules under test (repoint on migration) ---
from job_search.dates import (
    parse_iso_date,
    parse_epoch_date,
    parse_email_date,
    parse_rss_date,
)


def test_parse_iso_date():
    assert parse_iso_date("2024-03-15T10:00:00Z") == dt.date(2024, 3, 15)
    assert parse_iso_date("2024-03-15") == dt.date(2024, 3, 15)
    assert parse_iso_date("2024/03/15") == dt.date(2024, 3, 15)
    assert parse_iso_date("") is None
    assert parse_iso_date(None) is None
    assert parse_iso_date("not a date") is None


def test_parse_epoch_date():
    # 2021-01-01 12:00:00 UTC — noon keeps the local date stable across timezones.
    assert parse_epoch_date(1609502400) == dt.date(2021, 1, 1)
    assert parse_epoch_date("1609502400") == dt.date(2021, 1, 1)
    assert parse_epoch_date(None) is None
    assert parse_epoch_date("") is None
    assert parse_epoch_date("notanumber") is None


def test_parse_email_date():
    assert parse_email_date("Mon, 15 Mar 2024 10:00:00 +0000") == dt.date(2024, 3, 15)
    assert parse_email_date("") is None
    assert parse_email_date(None) is None


def test_parse_rss_date_is_email_date():
    assert parse_rss_date("Mon, 15 Mar 2024 10:00:00 +0000") == dt.date(2024, 3, 15)
    assert parse_rss_date("") is None
