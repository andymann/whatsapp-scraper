# whatsapp-scraper

Lists WhatsApp messages from every chat within a timeframe as a
`sender | message` table, and exports the full records as CSV/JSON.

| Script                    | What it reads                         | Runs on               | Needs                         |
|---------------------------|---------------------------------------|-----------------------|-------------------------------|
| `whatsapp_messages.py`    | The WhatsApp **desktop app** database | macOS only            | Python only                   |
| `whatsapp_web_scraper.py` | **web.whatsapp.com** (command line)   | macOS, Linux, Windows | Playwright + Chromium         |
| `whatsapp_web_gui.py`     | **web.whatsapp.com** (desktop window) | macOS, Linux, Windows | Playwright + Chromium + Tkinter |

Contents: [Setup](#setup) · [Running](#running) ·
[Building a binary](#building-a-binary-pyinstaller) ·
[Troubleshooting](#troubleshooting) · [How each script works](#whatsapp_messagespy)

## Setup

Everything runs in a Python virtual environment (venv), so Playwright and
its browser stay out of your system Python. You need **Python 3.10 or
newer with Tkinter (Tk 8.6+)**.

### macOS

Don't use Apple's built-in `/usr/bin/python3` (Python 3.9 from the Command
Line Tools): it ships the deprecated Tk 8.5 and the GUI opens as an **empty
dark window**. Install Python from [Homebrew](https://brew.sh) instead:

```bash
brew install python@3.13 python-tk@3.13
```

Then, from the repo folder:

```bash
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m playwright install chromium
```

### Linux (Debian/Ubuntu)

```bash
sudo apt install python3 python3-venv python3-tk
```

Then, from the repo folder:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m playwright install --with-deps chromium
```

`--with-deps` also installs the system libraries Chromium needs (it asks
for your sudo password). Other distributions: install Python 3.10+, its
`venv` and `tkinter` packages, then run the same commands.

### Windows (PowerShell)

Install Python 3.13 from [python.org](https://www.python.org/downloads/)
(it includes Tkinter), or with:

```powershell
winget install Python.Python.3.13
```

Then, from the repo folder:

```powershell
py -3.13 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m playwright install chromium
```

If PowerShell refuses to run `Activate.ps1` ("running scripts is disabled
on this system"), allow scripts for your user once, then activate again:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

In `cmd.exe` activate with `.venv\Scripts\activate.bat` instead.

### Check the setup

With the venv active, this should print a Tk version of 8.6 or newer:

```bash
python -c "import tkinter, playwright; print('Tk', tkinter.TkVersion, '- playwright OK')"
```

### Activating and leaving the venv

Activate the venv in every new terminal before running the scripts. Your
prompt then starts with `(.venv)`.

| Shell                     | Activate                          |
|---------------------------|-----------------------------------|
| macOS / Linux (bash, zsh) | `source .venv/bin/activate`       |
| fish                      | `source .venv/bin/activate.fish`  |
| Windows PowerShell        | `.venv\Scripts\Activate.ps1`      |
| Windows cmd               | `.venv\Scripts\activate.bat`      |

Leave it with `deactivate`. To start over, delete the `.venv` folder and
repeat the setup.

## Running

With the venv active, from the repo folder. The commands are the same on
macOS, Linux and Windows.

**GUI**

```bash
python whatsapp_web_gui.py
```

**WhatsApp Web, command line.** On the first run a Chromium window opens
with a QR code. Scan it in WhatsApp on your phone: **Settings → Linked
devices → Link a device**. The login is kept in `.wweb_profile/`, so later
runs can be headless.

```bash
python whatsapp_web_scraper.py --hours 24
python whatsapp_web_scraper.py --chats-limit 5 --days 2 --headless --export messages.json
```

**WhatsApp desktop app database (macOS only).** Needs the WhatsApp app
from the Mac App Store, installed and linked to your phone.

```bash
python whatsapp_messages.py
python whatsapp_messages.py --days 7 --export messages.csv
```

If reading `ChatStorage.sqlite` fails with a permission error, give your
terminal app **Full Disk Access** in System Settings → Privacy & Security.

## Building a binary (PyInstaller)

Turns the GUI into a standalone program with Chromium bundled inside, so it
runs without Python or a venv. Build on the system you want to run it on:
PyInstaller can't cross-compile, and the result only runs on the same OS
and processor type (e.g. Apple Silicon vs. Intel). Expect 400–550 MB,
mostly Chromium.

All commands run from the repo folder with the venv active. Every build
installs PyInstaller, deletes the previous `build/` and `dist/` folders,
runs PyInstaller and copies Chromium into the result. The app keeps its
WhatsApp login in your user data folder (listed below), not in the repo,
so you scan the QR code once more the first time you use it.

### macOS → `dist/WhatsApp Scraper.app`

One command does everything, including the ad-hoc code signing:

```bash
./build_macos_app.sh
```

Or the same steps by hand:

```bash
python -m pip install -r requirements-build.txt
rm -rf build dist
python -m PyInstaller --noconfirm --windowed --name "WhatsApp Scraper" --osx-bundle-identifier info.andyland.whatsapp-scraper --collect-all playwright --add-data "config.json:." whatsapp_web_gui.py
PLAYWRIGHT_BROWSERS_PATH=build/ms-playwright python -m playwright install --no-shell chromium
cp -R build/ms-playwright "dist/WhatsApp Scraper.app/Contents/Resources/ms-playwright"
codesign --force --deep --sign - "dist/WhatsApp Scraper.app"
```

Chromium is copied in *after* PyInstaller because PyInstaller can't
re-sign Chrome's nested `.app` itself.

Run it by double-clicking it in Finder, dragging it into **Applications**,
or with:

```bash
open "dist/WhatsApp Scraper.app"
```

The app isn't notarized. On your own Mac it opens normally; after copying
it to another Mac, right-click it → **Open** the first time, or run:

```bash
xattr -dr com.apple.quarantine "WhatsApp Scraper.app"
```

Login data: `~/Library/Application Support/WhatsApp Scraper/`

### Linux → `dist/WhatsApp Scraper/WhatsApp Scraper`

```bash
python -m pip install -r requirements-build.txt
rm -rf build dist
python -m PyInstaller --noconfirm --windowed --name "WhatsApp Scraper" --collect-all playwright --add-data "config.json:." whatsapp_web_gui.py
PLAYWRIGHT_BROWSERS_PATH="dist/WhatsApp Scraper/ms-playwright" python -m playwright install --no-shell chromium
```

Run it with:

```bash
"dist/WhatsApp Scraper/WhatsApp Scraper"
```

Copy the whole `dist/WhatsApp Scraper/` folder to move it; the executable
doesn't work on its own. The machine that runs it needs Chromium's system
libraries; the `--with-deps` step in the [Linux setup](#linux-debianubuntu)
installs them.

Login data: `~/.local/share/whatsapp-scraper/`

### Windows → `dist\WhatsApp Scraper\WhatsApp Scraper.exe`

```powershell
python -m pip install -r requirements-build.txt
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
python -m PyInstaller --noconfirm --windowed --name "WhatsApp Scraper" --collect-all playwright --add-data "config.json;." whatsapp_web_gui.py
$env:PLAYWRIGHT_BROWSERS_PATH = "dist\WhatsApp Scraper\ms-playwright"; python -m playwright install --no-shell chromium; Remove-Item Env:PLAYWRIGHT_BROWSERS_PATH
```

Run it by double-clicking `WhatsApp Scraper.exe` in that folder, or with:

```powershell
& "dist\WhatsApp Scraper\WhatsApp Scraper.exe"
```

Copy the whole `dist\WhatsApp Scraper\` folder to move it; the `.exe`
doesn't work on its own. Windows SmartScreen may warn about an unknown
publisher the first time: click **More info → Run anyway**.

Login data: `%APPDATA%\WhatsApp Scraper\`

> The macOS build is tested. The Linux and Windows builds use the same
> approach but haven't been tested on those systems yet.

## What stays local (and out of git)

`.gitignore` keeps personal data and build output out of the repository:

- `.venv/`: the virtual environment
- `.wweb_profile/`: your WhatsApp Web login session. **Treat it like a
  password**: anyone with this folder can read your chats.
- `*.csv`, `*.json` (except `config.json`): exported messages
- `*.sqlite*`: copies of the WhatsApp database
- `*.txt` / `*.log` (except the `requirements*.txt` files): saved GUI logs
- `build/`, `dist/`, `*.spec`: PyInstaller output
- `__pycache__/`: Python bytecode

Before pushing, run `git status` and check that none of these appear.

## Troubleshooting

| Problem | Fix |
|---|---|
| `ModuleNotFoundError: No module named 'playwright'` | The venv isn't active. Activate it (see [Setup](#activating-and-leaving-the-venv)). |
| `Executable doesn't exist ... chromium` | Run `python -m playwright install chromium` with the venv active. |
| GUI window is empty/dark; terminal shows `DEPRECATION WARNING: The system version of Tk is deprecated` | macOS: the venv uses Apple's Python 3.9 with Tk 8.5. Install Homebrew Python, delete `.venv` and redo the [macOS setup](#macos). |
| `python` runs the wrong Python although `python3.13` works | The venv was created on top of an old one. Delete `.venv` completely (`rm -rf .venv`), then create it again. |
| `ModuleNotFoundError: No module named '_tkinter'` | Install Tkinter (see [Setup](#setup) for your OS), then delete `.venv` and create it again. |
| `Could not find ChatStorage.sqlite` | The WhatsApp desktop app isn't installed (macOS only). Use the web scraper or GUI instead. |
| QR code never appears / login times out | Run without `--headless`, or raise `--login-timeout`. |
| `Could not read WhatsApp's internal data` | WhatsApp Web changed internally; see [How this actually extracts messages](#how-this-actually-extracts-messages-not-dom-scraping). |

---

## whatsapp_messages.py

Lists WhatsApp (macOS desktop app) messages from every chat, within a
configurable timeframe, in one place — as a `sender | message` table — and
exports the full structured records for further processing.

### How the schema was determined

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

### Usage

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

### Output shape

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

See [Setup](#setup) at the top for your OS.

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
