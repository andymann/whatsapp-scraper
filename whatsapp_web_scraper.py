#!/usr/bin/env python3
"""
whatsapp_web_scraper.py

Produces the same output as whatsapp_messages.py (a sender | message table,
plus structured export) but sourced from https://web.whatsapp.com/ via a
real browser (Playwright) instead of the local ChatStorage.sqlite file.
Only the N newest conversations (default 10) are scraped.

Requires:
    pip install playwright
    playwright install chromium

First run opens a visible Chromium window showing a QR code — scan it with
WhatsApp on your phone (Settings > Linked devices > Link a device). The
session is persisted in --profile-dir, so you normally only do this once.

HOW THIS WORKS (and why it's not DOM scraping)

An earlier version of this script tried to read message text out of the
rendered DOM. That turned out to be a dead end: WhatsApp Web's message list
goes through a transitional/virtualized rendering phase, and the DOM content
Playwright could query was reliably out of sync with what was actually
painted on screen — confirmed by comparing a Playwright screenshot (showing
real messages) against a DOM dump taken at the same instant (showing empty
placeholders).

Instead, this reads WhatsApp's own in-memory JavaScript data model directly
— the same technique used by mature tools like whatsapp-web.js. WhatsApp
Web exposes a global `require(moduleName)` function (Meta's internal/Haste
module system). `require('WAWebCollections')` returns live Backbone-style
collections, including `Chat` (every conversation) and, per chat, a `msgs`
sub-collection of its messages — with clean fields (`t` for timestamp,
`body` for text, `id.fromMe`, `author`, etc.), no rendering involved at all.

This is still coupled to WhatsApp's internal module names/fields and can
break on a WhatsApp Web update — just less fragile than fighting virtualized
DOM rendering. If `require('WAWebCollections')` ever stops working, open
DevTools in the launched browser and re-run the discovery snippets described
in README.md to find the new names.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from wa_common import (
    MessageRecord,
    export_records,
    load_config,
    print_table,
    resolve_timeframe,
    to_table_rows,
)

# Bumped whenever this file's extraction logic changes, and logged at the
# start of every fetch — since Python only reads a module's source once per
# process, this is the only reliable way to confirm a running GUI/CLI is
# actually using the latest edits rather than a stale in-memory copy.
SCRAPER_VERSION = "2026-10-22-internal-api-01"

DEFAULT_CONFIG_PATH = Path(__file__).with_name("config.json")
DEFAULT_PROFILE_DIR = Path(__file__).with_name(".wweb_profile")

# Only used to detect "are we logged in" (the chat list sidebar) — this part
# of the DOM was never the problem; it's long stable across WhatsApp Web
# releases, unlike the message-content rendering.
CHAT_LIST_SELECTOR = '[aria-label="Chat list"], #pane-side, [data-testid="chat-list"]'

# Runs inside the page via page.evaluate(). Reads WhatsApp's internal data
# model directly: no scrolling, no clicking into chats, no DOM text parsing.
EXTRACTION_JS = """
({ chatsLimit, startEpoch, endEpoch }) => {
  if (typeof window.require !== 'function') {
    return { error: "window.require is not available on this page" };
  }
  let Chat;
  try {
    ({ Chat } = window.require('WAWebCollections'));
  } catch (e) {
    return { error: "require('WAWebCollections') failed: " + e.message };
  }
  if (!Chat || typeof Chat.getModelsArray !== 'function') {
    return { error: "Chat collection missing or has an unexpected shape" };
  }

  const chats = Chat.getModelsArray()
    .slice()
    .sort((a, b) => (b.attributes.t || 0) - (a.attributes.t || 0))
    .slice(0, chatsLimit);

  const results = [];
  for (const chat of chats) {
    const a = chat.attributes;
    const chatId = typeof a.id === 'string'
      ? a.id
      : (a.id && a.id.toString ? a.id.toString() : String(a.id));
    const chatName = a.name || a.formattedTitle || chatId;
    const isGroup = typeof chatId === 'string' && chatId.endsWith('@g.us');

    const msgModels = (chat.msgs && typeof chat.msgs.getModelsArray === 'function')
      ? chat.msgs.getModelsArray()
      : [];

    for (const msg of msgModels) {
      const ma = msg.attributes;
      const t = ma.t;
      if (t == null || t < startEpoch || t > endEpoch) continue;

      const isFromMe = !!(ma.id && ma.id.fromMe);
      let text;
      if (ma.type === 'chat' || ma.type === 'text') {
        text = ma.body || '';
      } else {
        text = ma.caption || ('[' + (ma.type || 'media') + ' message]');
      }

      const authorJid = ma.author
        ? (ma.author.toString ? ma.author.toString() : ma.author)
        : null;
      const msgId = (ma.id && ma.id.id) ? ma.id.id : null;

      results.push({
        chatId, chatName, isGroup, t, isFromMe,
        senderJid: isFromMe ? null : authorJid,
        notifyName: ma.notifyName || null,
        text, msgId,
      });
    }
  }
  return { chatsSeen: chats.length, messages: results };
}
"""


def strip_jid(jid: Optional[str]) -> Optional[str]:
    """Reduce a WhatsApp JID like '4917012345@s.whatsapp.net' to just the id part."""
    if not jid:
        return None
    return jid.split("@", 1)[0]


def launch_new_browser_context(playwright, profile_dir: Path, headless: bool):
    """Launch a Playwright-managed, persistent Chromium profile (QR-login flow)."""
    context = playwright.chromium.launch_persistent_context(
        user_data_dir=str(profile_dir),
        headless=headless,
        viewport={"width": 1280, "height": 900},
    )
    page = context.pages[0] if context.pages else context.new_page()
    return context, page


def attach_to_existing_browser(playwright, cdp_endpoint: str):
    """
    Attach to an already-running Chrome/Chromium via the DevTools Protocol.

    The target browser must have been *launched* with e.g.
    --remote-debugging-port=9222 (CDP cannot be enabled after the fact on an
    already-running instance). If a tab already has web.whatsapp.com open
    (and logged in), that tab is reused as-is — no new login required.
    """
    browser = playwright.chromium.connect_over_cdp(cdp_endpoint)
    context = browser.contexts[0] if browser.contexts else browser.new_context()

    page = next((p for p in context.pages if "web.whatsapp.com" in p.url), None)
    if page is None:
        page = context.new_page()
        page.goto("https://web.whatsapp.com")

    return browser, context, page


def is_logged_in(page, timeout_ms: int = 4000) -> bool:
    try:
        page.wait_for_selector(CHAT_LIST_SELECTOR, timeout=timeout_ms)
        return True
    except Exception:
        return False


def wait_for_login(page, timeout_seconds: int, log=lambda msg: print(msg, file=sys.stderr)) -> None:
    if is_logged_in(page, timeout_ms=5000):
        return

    log(
        "Scan the QR code in the opened browser window with WhatsApp "
        "(Settings > Linked devices > Link a device)..."
    )
    page.wait_for_selector(CHAT_LIST_SELECTOR, timeout=timeout_seconds * 1000)


def scrape_from_page(
    page,
    chats_limit: int,
    start: datetime,
    end: datetime,
    log=lambda msg: print(msg, file=sys.stderr),
) -> list[MessageRecord]:
    """
    Pull messages for the N newest conversations directly from WhatsApp
    Web's in-memory data model (see module docstring). Works equally for a
    freshly launched browser or one attached via CDP.
    """
    log(f"(whatsapp_web_scraper version {SCRAPER_VERSION})")

    start_epoch = int(start.timestamp())
    end_epoch = int(end.timestamp())

    raw = page.evaluate(
        EXTRACTION_JS,
        {"chatsLimit": chats_limit, "startEpoch": start_epoch, "endEpoch": end_epoch},
    )

    if not isinstance(raw, dict) or raw.get("error"):
        error = raw.get("error") if isinstance(raw, dict) else "unexpected response shape"
        log(f"Could not read WhatsApp's internal data: {error}")
        return []

    chats_seen = raw.get("chatsSeen", 0)
    messages = raw.get("messages", [])
    log(f"Scanned {chats_seen} newest chat(s), found {len(messages)} message(s) in range.")

    records: list[MessageRecord] = []
    for m in messages:
        is_from_me = bool(m.get("isFromMe"))
        is_group = bool(m.get("isGroup"))
        chat_name = m.get("chatName") or strip_jid(m.get("chatId")) or "Unknown chat"
        sender_jid = m.get("senderJid")

        if is_from_me:
            sender = "Me"
        elif is_group:
            sender = m.get("notifyName") or strip_jid(sender_jid) or "Unknown"
        else:
            sender = chat_name or strip_jid(sender_jid) or strip_jid(m.get("chatId")) or "Unknown"

        records.append(
            MessageRecord(
                timestamp=datetime.fromtimestamp(m["t"], tz=timezone.utc).isoformat(),
                chat_id=str(m.get("chatId")),
                chat_name=chat_name,
                is_group=is_group,
                sender=sender,
                sender_jid=sender_jid,
                is_from_me=is_from_me,
                message_id=m.get("msgId"),
                message=m.get("text") or "[non-text message]",
            )
        )

    records.sort(key=lambda r: r.timestamp)
    return records


def scrape(
    chats_limit: int,
    start: datetime,
    end: datetime,
    profile_dir: Path,
    headless: bool,
    login_timeout: int,
) -> list[MessageRecord]:
    """CLI entry point: owns the full Playwright lifecycle (launch, login, close)."""
    from playwright.sync_api import sync_playwright  # imported lazily so the
    # rest of the project stays dependency-free unless this script is used.

    with sync_playwright() as playwright:
        context, page = launch_new_browser_context(playwright, profile_dir, headless)
        page.goto("https://web.whatsapp.com")
        wait_for_login(page, login_timeout)

        records = scrape_from_page(page, chats_limit, start, end)
        context.close()

    return records


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="List the N newest WhatsApp Web conversations' messages within a "
                    "timeframe, as a sender | message table (same output as whatsapp_messages.py)."
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--chats-limit", type=int, default=10, help="How many newest conversations to scan")
    parser.add_argument("--profile-dir", type=Path, default=DEFAULT_PROFILE_DIR, help="Persistent browser profile (keeps you logged in)")
    parser.add_argument("--headless", action="store_true", help="Run headless (only works after the first login)")
    parser.add_argument("--login-timeout", type=int, default=120, help="Seconds to wait for QR-code login")

    timeframe = parser.add_argument_group("timeframe")
    timeframe.add_argument("--since", help="Start, ISO date/datetime")
    timeframe.add_argument("--until", help="End, ISO date/datetime (default: now)")
    timeframe.add_argument("--days", type=float)
    timeframe.add_argument("--hours", type=float)
    timeframe.add_argument("--minutes", type=float)

    parser.add_argument("--max-width", type=int, default=80)
    parser.add_argument("--export", type=Path, help="Write full structured records to this file")
    parser.add_argument("--format", choices=["csv", "json"])
    parser.add_argument("--quiet", action="store_true")

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    config = load_config(args.config)

    start, end = resolve_timeframe(
        args.since, args.until, args.days, args.hours, args.minutes,
        default_hours=config.get("default_timeframe_hours", 24),
    )

    try:
        records = scrape(
            chats_limit=args.chats_limit,
            start=start,
            end=end,
            profile_dir=args.profile_dir,
            headless=args.headless,
            login_timeout=args.login_timeout,
        )
    except ImportError:
        print(
            "Playwright is required for this script:\n"
            "    pip install playwright\n"
            "    playwright install chromium",
            file=sys.stderr,
        )
        return 1

    if not args.quiet:
        print(f"\nMessages from {start.isoformat()} to {end.isoformat()} ({len(records)} found)\n")
        print_table(to_table_rows(records), max_message_width=args.max_width)

    export_path = args.export or (Path(config["default_export_path"]) if config.get("default_export_path") else None)
    if export_path:
        fmt = args.format or config.get("default_export_format") or export_path.suffix.lstrip(".") or "csv"
        export_records(records, export_path, fmt)
        print(f"\nWrote {len(records)} records to {export_path} ({fmt}).")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
