"""Date parsers for the various source date formats."""
import datetime as dt
import email.utils


def parse_iso_date(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None

    normalized = text.replace("Z", "+00:00")
    if hasattr(dt.datetime, "fromisoformat"):
        try:
            return dt.datetime.fromisoformat(normalized).date()
        except (TypeError, ValueError):
            pass
    if hasattr(dt.date, "fromisoformat"):
        try:
            return dt.date.fromisoformat(text[:10])
        except (TypeError, ValueError):
            pass
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return dt.datetime.strptime(text[:10], fmt).date()
        except (TypeError, ValueError):
            pass
    return None


def parse_epoch_date(value):
    if value is None or value == "":
        return None
    try:
        return dt.datetime.fromtimestamp(int(value)).date()
    except (TypeError, ValueError, OSError):
        return None


def parse_email_date(value):
    if not value:
        return None
    try:
        return email.utils.parsedate_to_datetime(value).date()
    except (TypeError, ValueError):
        return None


def parse_rss_date(text):
    return parse_email_date(text)
