# whatsapp_messages.py

> **Setup:** see [VENV_SETUP.md](VENV_SETUP.md) for running everything in a Python virtual environment.

Lists WhatsApp (macOS desktop app) messages from every chat, within a
configurable timeframe, in one place — as a `sender | message` table — and
exports the full structured records for further processing.

## How the schema was determined

Reverse-engineered from the WhatsApp.app bundle itself (`WhatsApp.app/`),
no guessing:

- **Core Data model**: `WhatsApp.app/Contents/Resources/WhatsAppChat.momd`,
  current version `WhatsAppChat_2.18.10` (per `VersionInfo.plist`). Entity
  and attribute names were extracted directly from the compiled model.
- **Table/column mapping**: Core Data's SQLite store uses the convention
  `Z<ENTITY>` / `Z<ATTRIBUTE>` (all uppercase). Confirmed entities used here:
  - `WAMessage` → `ZWAMESSAGE` (`ZTEXT`, `ZMESSAGEDATE`, `ZISFROMME`,
    `ZFROMJID`, `ZCHATSESSION`, `ZGROUPMEMBER`, `ZSTANZAID`)
  - `WAChatSession` → `ZWACHATSESSION` (`ZCONTACTJID`, `ZPARTNERNAME`,
    `ZGROUPINFO` — non-null means it's a group chat)
  - `WAGroupMember` → `ZWAGROUPMEMBER` (`ZMEMBERJID`, `ZCONTACTNAME`,
    `ZCHATSESSION`)
- **Database location**: the app's code-signing entitlements declare app
  group `group.net.whatsapp.WhatsApp.shared`, which macOS places at
  `~/Library/Group Containers/group.net.whatsapp.WhatsApp.shared/ChatStorage.sqlite`.
- **Timestamps**: Core Data stores `NSDate` as seconds since the Cocoa
  reference date, `2001-01-01T00:00:00Z`.

## Usage

```bash
# Last 24h (default), printed as a table
python3 whatsapp_messages.py

# Explicit range
python3 whatsapp_messages.py --since 2026-10-01 --until 2026-10-22

# Relative range + export full records for further processing
python3 whatsapp_messages.py --days 7 --export messages.csv

# Only one chat (substring match on chat name / sender name / JID)
python3 whatsapp_messages.py --hours 12 --chat "Family"

# Point at a specific/backed-up database
python3 whatsapp_messages.py --db /path/to/ChatStorage.sqlite --days 1
```

Defaults (db path, timeframe, export format/path, chat filter) live in
[`config.json`](config.json) and are overridden by CLI flags.

## Output shape

**Console / table view** — exactly 2 columns, as requested:

```
Sender | Message
-----------------
Mom    | Hi honey
Me     | Hey Mom!
```

**`--export` (CSV/JSON)** — the same messages as full structured records,
ready for downstream processing (pandas, filtering, dedup, etc.):

```json
{
  "timestamp": "2026-10-22T05:10:41+00:00",
  "chat_id": "1",
  "chat_name": "Mom",
  "is_group": false,
  "sender": "Mom",
  "sender_jid": "491701234567@s.whatsapp.net",
  "is_from_me": false,
  "message_id": "stz1",
  "message": "Hi honey"
}
```

No external dependencies — standard library only. The script never writes
to the live WhatsApp database: it copies `ChatStorage.sqlite` (and any
`-wal`/`-shm` sidecars) to a temp directory before reading.

---

## whatsapp_web_scraper.py — same output, sourced from web.whatsapp.com

Produces the identical `sender | message` table and CSV/JSON export (both
scripts share [`wa_common.py`](wa_common.py) for the data model, timeframe
handling, table rendering, and export), but by driving a real browser
against **https://web.whatsapp.com/** instead of reading a local database.
Only scrapes the **N newest conversations** (default 10).

### Setup

```bash
pip install -r requirements.txt
playwright install chromium
```

### Usage

```bash
# First run: a visible Chromium window opens showing a QR code.
# Scan it with WhatsApp on your phone (Settings > Linked devices > Link a device).
python3 whatsapp_web_scraper.py --hours 24

# Fewer/more conversations, headless after the first login, export
python3 whatsapp_web_scraper.py --chats-limit 5 --days 2 --headless --export messages.json
```

The login session is kept in `.wweb_profile/` (a persistent Chromium
profile, `--profile-dir` to change it), so you normally scan the QR code
only once.

### How this actually extracts messages (not DOM scraping)

An earlier version of this script read message text out of the rendered
DOM (matching `data-pre-plain-text`/`data-id`/`message-in`/`message-out`).
That turned out to be a dead end specific to this WhatsApp Web build: its
message list goes through a transitional/virtualized rendering phase, and
the DOM content was reliably out of sync with what was actually on screen
— confirmed by comparing a Playwright screenshot (showing real messages)
against a DOM dump taken at the exact same instant (showing only empty
placeholder `<div></div>` elements). No amount of waiting or retrying
fixed that, because the text genuinely wasn't in the DOM at the time.

Instead, this reads WhatsApp's own in-memory JavaScript data model
directly — the technique mature tools like
[whatsapp-web.js](https://github.com/pedroslopez/whatsapp-web.js) use.
WhatsApp Web exposes a global `require(moduleName)` function (Meta's
internal "Haste" module system). `require('WAWebCollections')` returns
live data collections, including `Chat` (every conversation, each with
its own `msgs` sub-collection) with clean fields: `t` (Unix timestamp),
`body` (text), `id.fromMe`, `author` (group sender), `notifyName`, etc.
No rendering, scrolling, or clicking involved — `scrape_from_page` is one
`page.evaluate()` call (see `EXTRACTION_JS` in
[`whatsapp_web_scraper.py`](whatsapp_web_scraper.py)).

This is still coupled to WhatsApp's internal module name and field names,
and can break on a WhatsApp Web update — just a fundamentally more stable
foundation than fighting virtualized DOM rendering. If
`require('WAWebCollections')` ever stops working (the log will say
`Could not read WhatsApp's internal data: ...`), open DevTools in the
launched browser and re-run this discovery sequence to find the new
names:

```js
// 1. Confirm the module loader is still there and find the collections
Object.keys(window.require('WAWebCollections'))

// 2. Inspect a chat's shape
const { Chat } = window.require('WAWebCollections');
Chat.getModelsArray()[0].attributes

// 3. Inspect that chat's messages
Chat.getModelsArray()[0].msgs.getModelsArray()[0].attributes
```

---

## whatsapp_web_gui.py — desktop UI

A Tkinter UI (standard library, no extra GUI dependency) around
`whatsapp_web_scraper.py`: connect, pick a timeframe and chat count, and
browse the results live in a table. Check **"Auto-fetch every 30s"** to
have it refetch automatically on a timer instead of clicking Fetch
manually — it skips a tick rather than queuing up if a fetch is still in
progress, or if nothing's connected yet.

```bash
python3 whatsapp_web_gui.py
```

Two ways to connect:

1. **Launch new browser** — opens a Chromium window at web.whatsapp.com
   with a QR code to scan. Session persists in `.wweb_profile/`, so this
   is normally one-time.
2. **Connect to existing Chrome (CDP)** — attaches to an already-running
   Chrome over the DevTools Protocol instead of opening a new browser.

   **The real constraint here**: Chrome cannot expose its debugging port
   on a tab that's already open — the *whole browser process* has to be
   started with `--remote-debugging-port=<port>`, and Chrome ignores that
   flag if you pass it while another instance of Chrome is already running
   (it just forwards the request to the existing process). So "point at a
   tab I'm already logged into" isn't literally possible for a Chrome that
   wasn't launched this way from the start.

   The practical equivalent, which the GUI's **"Launch debuggable
   Chrome..."** button does for you: it checks Chrome isn't currently
   running (and tells you to quit it if it is), then relaunches Chrome
   pointed at your *normal* profile directory with the debug port enabled.
   Because it's your real profile, your existing WhatsApp Web session
   (stored on disk from ordinary use) comes back automatically — no new
   QR scan. Click **Connect** once that window has loaded.

   If you already run Chrome with `--remote-debugging-port` by habit (e.g.
   via a dedicated shortcut), you can skip the launch button and just
   enter its endpoint (default `http://localhost:9222`) and Connect — this
   is the one case that's a genuine "point at an already-open, already-
   logged-in tab."

Note: an open CDP debugging port accepts commands from any local process
while Chrome is running that way — fine for your own machine, but don't
leave it open on a shared one.
