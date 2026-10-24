#!/usr/bin/env python3
"""
whatsapp_messages.py

Reads the local WhatsApp (macOS desktop app) message store and lists every
message across every chat within a configurable timeframe, in one place.

Schema grounded in reverse engineering of WhatsApp.app (see REA_NOTES.md):
  - Core Data model: WhatsAppChat.momd (current version "WhatsAppChat_2.18.10")
  - Entities -> SQLite tables (Core Data NSSQLiteStore convention: Z + NAME):
      WAMessage      -> ZWAMESSAGE     (ZTEXT, ZMESSAGEDATE, ZISFROMME,
                                         ZFROMJID, ZCHATSESSION, ZGROUPMEMBER)
      WAChatSession  -> ZWACHATSESSION (ZCONTACTJID, ZPARTNERNAME,
                                         ZGROUPINFO)
      WAGroupMember  -> ZWAGROUPMEMBER (ZMEMBERJID, ZCONTACTNAME, ZCHATSESSION)
  - App group container (from code-signing entitlements):
      group.net.whatsapp.WhatsApp.shared
  - Dates are Core Data "reference dates": seconds since 2001-01-01 UTC.

This module only reads the database (via a throwaway copy) and never
modifies the user's WhatsApp data.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, Optional

from wa_common import (
    MessageRecord,
    export_records,
    load_config,
    print_table,
    resolve_timeframe,
    to_table_rows,
)

# Core Data stores NSDate as seconds since this reference instant (UTC).
COCOA_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)

DEFAULT_CONFIG_PATH = Path(__file__).with_name("config.json")
DEFAULT_GROUP_CONTAINER = (
    Path.home()
    / "Library"
    / "Group Containers"
    / "group.net.whatsapp.WhatsApp.shared"
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def cocoa_to_datetime(value: float) -> datetime:
    return COCOA_EPOCH + timedelta(seconds=value)


def datetime_to_cocoa(value: datetime) -> float:
    if value.tzinfo is None:
        value = value.astimezone()  # assume local time zone
    return (value.astimezone(timezone.utc) - COCOA_EPOCH).total_seconds()


def strip_jid(jid: Optional[str]) -> Optional[str]:
    """Reduce a WhatsApp JID like '4917012345@s.whatsapp.net' to just the id part."""
    if not jid:
        return None
    return jid.split("@", 1)[0]


def find_default_db() -> Optional[Path]:
    """Locate ChatStorage.sqlite in the WhatsApp app-group container."""
    direct = DEFAULT_GROUP_CONTAINER / "ChatStorage.sqlite"
    if direct.exists():
        return direct
    if DEFAULT_GROUP_CONTAINER.exists():
        matches = list(DEFAULT_GROUP_CONTAINER.rglob("ChatStorage.sqlite"))
        if matches:
            return matches[0]
    return None


@contextmanager
def open_readonly_copy(db_path: Path) -> Iterator[sqlite3.Connection]:
    """
    Copy the database (plus any -wal/-shm sidecars) to a temp dir and open
    the copy, so we never lock or modify the live WhatsApp database.
    """
    tmpdir = Path(tempfile.mkdtemp(prefix="wa_chatstorage_"))
    try:
        tmp_db = tmpdir / db_path.name
        shutil.copy2(db_path, tmp_db)
        for suffix in ("-wal", "-shm"):
            sidecar = db_path.with_name(db_path.name + suffix)
            if sidecar.exists():
                shutil.copy2(sidecar, tmpdir / sidecar.name)

        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# --------------------------------------------------------------------------- #
# Core query
# --------------------------------------------------------------------------- #

def fetch_messages(
    conn: sqlite3.Connection,
    start: datetime,
    end: datetime,
    chat_filter: Optional[str] = None,
    limit: Optional[int] = None,
) -> list[MessageRecord]:
    query = """
        SELECT
            m.Z_PK           AS message_pk,
            m.ZMESSAGEDATE   AS raw_date,
            m.ZTEXT          AS text,
            m.ZISFROMME      AS is_from_me,
            m.ZFROMJID       AS from_jid,
            m.ZSTANZAID      AS stanza_id,
            cs.Z_PK          AS chat_pk,
            cs.ZCONTACTJID   AS chat_jid,
            cs.ZPARTNERNAME  AS chat_partner_name,
            cs.ZGROUPINFO    AS chat_group_info,
            gm.ZMEMBERJID    AS group_member_jid,
            gm.ZCONTACTNAME  AS group_member_name
        FROM ZWAMESSAGE m
        LEFT JOIN ZWACHATSESSION cs ON cs.Z_PK = m.ZCHATSESSION
        LEFT JOIN ZWAGROUPMEMBER gm ON gm.Z_PK = m.ZGROUPMEMBER
        WHERE m.ZMESSAGEDATE BETWEEN ? AND ?
        ORDER BY m.ZMESSAGEDATE ASC
    """
    params: list = [datetime_to_cocoa(start), datetime_to_cocoa(end)]
    if limit:
        query += " LIMIT ?"
        params.append(limit)

    rows = conn.execute(query, params).fetchall()

    records: list[MessageRecord] = []
    for row in rows:
        is_group = row["chat_group_info"] is not None
        is_from_me = bool(row["is_from_me"])

        if is_from_me:
            sender = "Me"
            sender_jid = None
        elif is_group:
            sender = row["group_member_name"] or strip_jid(row["group_member_jid"]) or "Unknown"
            sender_jid = row["group_member_jid"]
        else:
            sender = row["chat_partner_name"] or strip_jid(row["from_jid"]) or strip_jid(row["chat_jid"]) or "Unknown"
            sender_jid = row["from_jid"] or row["chat_jid"]

        chat_name = row["chat_partner_name"] or strip_jid(row["chat_jid"]) or "Unknown chat"
        message = row["text"] if row["text"] is not None else "[non-text message]"

        record = MessageRecord(
            timestamp=cocoa_to_datetime(row["raw_date"]).isoformat(),
            chat_id=str(row["chat_pk"]),
            chat_name=chat_name,
            is_group=is_group,
            sender=sender,
            sender_jid=sender_jid,
            is_from_me=is_from_me,
            message_id=row["stanza_id"],
            message=message,
        )

        if chat_filter:
            needle = chat_filter.lower()
            haystack = f"{record.chat_name} {record.sender} {record.sender_jid or ''}".lower()
            if needle not in haystack:
                continue

        records.append(record)

    return records


# --------------------------------------------------------------------------- #
# Config + CLI
# --------------------------------------------------------------------------- #

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="List WhatsApp messages across all chats within a timeframe, "
                     "as a sender | message table."
    )
    parser.add_argument("--db", type=Path, help="Path to ChatStorage.sqlite (auto-detected if omitted)")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help="Path to config.json")

    timeframe = parser.add_argument_group("timeframe")
    timeframe.add_argument("--since", help="Start, ISO date/datetime, e.g. 2026-10-01 or 2026-10-01T09:00")
    timeframe.add_argument("--until", help="End, ISO date/datetime (default: now)")
    timeframe.add_argument("--days", type=float, help="Relative timeframe: last N days")
    timeframe.add_argument("--hours", type=float, help="Relative timeframe: last N hours")
    timeframe.add_argument("--minutes", type=float, help="Relative timeframe: last N minutes")

    parser.add_argument("--chat", help="Only include messages matching this chat/sender substring")
    parser.add_argument("--limit", type=int, help="Max number of messages to fetch")
    parser.add_argument("--max-width", type=int, default=80, help="Max message width in the printed table")

    parser.add_argument("--export", type=Path, help="Write full structured records to this file")
    parser.add_argument("--format", choices=["csv", "json"], help="Export format (default: inferred from --export suffix)")
    parser.add_argument("--quiet", action="store_true", help="Skip printing the console table")

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    config = load_config(args.config)

    db_path = args.db or (Path(config["db_path"]) if config.get("db_path") else None) or find_default_db()
    if db_path is None or not db_path.exists():
        print(
            "Could not find ChatStorage.sqlite. Pass --db /path/to/ChatStorage.sqlite "
            "or set \"db_path\" in config.json.\n"
            f"Default location checked: {DEFAULT_GROUP_CONTAINER / 'ChatStorage.sqlite'}",
            file=sys.stderr,
        )
        return 1

    start, end = resolve_timeframe(
        args.since, args.until, args.days, args.hours, args.minutes,
        default_hours=config.get("default_timeframe_hours", 24),
    )
    chat_filter = args.chat or config.get("chat_filter")

    with open_readonly_copy(db_path) as conn:
        records = fetch_messages(conn, start, end, chat_filter=chat_filter, limit=args.limit)

    if not args.quiet:
        print(f"Messages from {start.isoformat()} to {end.isoformat()} ({len(records)} found)\n")
        print_table(to_table_rows(records), max_message_width=args.max_width)

    export_path = args.export or (Path(config["default_export_path"]) if config.get("default_export_path") else None)
    if export_path:
        fmt = args.format or config.get("default_export_format") or export_path.suffix.lstrip(".") or "csv"
        export_records(records, export_path, fmt)
        print(f"\nWrote {len(records)} records to {export_path} ({fmt}).")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
