"""
Shared data model, timeframe resolution, table rendering, and export logic
used by both whatsapp_messages.py (local ChatStorage.sqlite reader) and
whatsapp_web_scraper.py (web.whatsapp.com scraper), so both tools produce
identical output shapes.
"""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional


@dataclass
class MessageRecord:
    """One message, with enough fields for later processing beyond the table view."""

    timestamp: str            # ISO 8601
    chat_id: str               # stable identifier for the chat (DB pk, or chat title)
    chat_name: str
    is_group: bool
    sender: str                # display name shown in the 2-column table
    sender_jid: Optional[str]
    is_from_me: bool
    message_id: Optional[str]
    message: str


def parse_user_datetime(value: str) -> datetime:
    """Parse a CLI date/datetime string; naive values are treated as local time."""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt


def resolve_timeframe(
    since: Optional[str],
    until: Optional[str],
    days: Optional[float],
    hours: Optional[float],
    minutes: Optional[float],
    default_hours: float = 24,
) -> tuple[datetime, datetime]:
    now = datetime.now().astimezone()

    if since or until:
        start = parse_user_datetime(since) if since else now - timedelta(days=36500)
        end = parse_user_datetime(until) if until else now
        return start, end

    for unit, value in (("days", days), ("hours", hours), ("minutes", minutes)):
        if value is not None:
            return now - timedelta(**{unit: value}), now

    return now - timedelta(hours=default_hours), now


def to_table_rows(records: list[MessageRecord]) -> list[tuple[str, str]]:
    """The exact 2-column shape requested: sender's name/number, then message."""
    return [(r.sender, r.message.replace("\n", " ⏎ ")) for r in records]


def print_table(rows: list[tuple[str, str]], max_message_width: int = 80) -> None:
    if not rows:
        print("No messages found in the given timeframe.")
        return

    sender_width = max(len("Sender"), *(len(s) for s, _ in rows))
    header = f"{'Sender'.ljust(sender_width)} | Message"
    print(header)
    print("-" * len(header))
    for sender, message in rows:
        if len(message) > max_message_width:
            message = message[: max_message_width - 1] + "…"
        print(f"{sender.ljust(sender_width)} | {message}")


def export_records(records: list[MessageRecord], path: Path, fmt: str) -> None:
    if fmt == "json":
        path.write_text(json.dumps([asdict(r) for r in records], indent=2, ensure_ascii=False))
    elif fmt == "csv":
        fieldnames = list(asdict(records[0]).keys()) if records else [
            "timestamp", "chat_id", "chat_name", "is_group", "sender",
            "sender_jid", "is_from_me", "message_id", "message",
        ]
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for r in records:
                writer.writerow(asdict(r))
    else:
        raise ValueError(f"Unsupported export format: {fmt}")


def load_config(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {}
