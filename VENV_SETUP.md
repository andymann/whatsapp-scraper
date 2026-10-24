# Running whatsapp-scraper in a virtual environment (venv)

This guide sets up an isolated Python environment for the three scripts in
this repo, so Playwright and its browser don't touch your system Python.

| Script                    | Needs                                   |
|---------------------------|-----------------------------------------|
| `whatsapp_messages.py`    | Python only (standard library)          |
| `whatsapp_web_scraper.py` | Playwright + Chromium                   |
| `whatsapp_web_gui.py`     | Playwright + Chromium + Tkinter         |

## 1. Prerequisites

- **Python 3.10 or newer** (developed against 3.14). Check with:

  ```bash
  python3 --version
  ```

  > **macOS:** don't use Apple's built-in `/usr/bin/python3` (Python 3.9
  > from the Command Line Tools). It ships the deprecated Tk 8.5, and the
  > GUI opens as an **empty dark window**. Install a current Python instead:
  >
  > ```bash
  > brew install python@3.13 python-tk@3.13
  > ```
  >
  > and use `python3.13` in step 3 below. The python.org installer works too.

- **Tkinter** (only for the GUI). It ships with the python.org installers;
  with Homebrew install `python-tk@<version>` as shown above.
  On Debian/Ubuntu: `sudo apt install python3-tk python3-venv`.

Verify Tkinter works and is Tk 8.6 or newer:

```bash
python3.13 -c "import tkinter; print(tkinter.TkVersion)"
```

## 2. Clone the repo

```bash
git clone https://github.com/andymann/whatsapp-scraper.git
cd whatsapp-scraper
```

## 3. Create and activate the venv

```bash
python3.13 -m venv .venv
```

(On Linux/Windows, `python3` / `py -3` is fine if it is 3.10+.)

Activate it (do this in every new terminal before running the scripts):

| Shell                    | Command                         |
|--------------------------|---------------------------------|
| macOS / Linux (bash/zsh) | `source .venv/bin/activate`     |
| fish                     | `source .venv/bin/activate.fish`|
| Windows PowerShell       | `.venv\Scripts\Activate.ps1`    |
| Windows cmd              | `.venv\Scripts\activate.bat`    |

Your prompt now starts with `(.venv)`. `python` and `pip` point into the venv.

> `.venv/` is listed in `.gitignore`, so it is never committed.

## 4. Install dependencies

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m playwright install chromium
```

The last command downloads a Playwright-managed Chromium (~150 MB) into
Playwright's cache (`~/Library/Caches/ms-playwright` on macOS,
`~/.cache/ms-playwright` on Linux), not into the repo.

On Linux you may also need the browser's system libraries:
`python -m playwright install-deps chromium`.

## 5. Run the scripts

All commands assume the venv is active.

### Local WhatsApp desktop database (macOS only)

```bash
python whatsapp_messages.py                 # last 24 h
python whatsapp_messages.py --days 7 --export messages.csv
```

If you get a permission error reading `ChatStorage.sqlite`, grant your
terminal app **Full Disk Access** in System Settings → Privacy & Security.

### WhatsApp Web scraper (CLI)

```bash
# First run: a Chromium window opens with a QR code.
# Scan it in WhatsApp on your phone: Settings → Linked devices → Link a device.
python whatsapp_web_scraper.py --hours 24

# Later runs can be headless; the login is kept in .wweb_profile/
python whatsapp_web_scraper.py --chats-limit 5 --days 2 --headless --export messages.json
```

### Desktop GUI

```bash
python whatsapp_web_gui.py
```

See `README.md` for all options and the two connection modes.

## 6. Deactivate / remove

```bash
deactivate          # leave the venv
rm -rf .venv        # delete it completely (recreate with steps 3–4)
```

## What stays local (and out of git)

The `.gitignore` keeps personal data out of the repository:

- `.venv/` — the virtual environment
- `.wweb_profile/` — your WhatsApp Web login session. **Treat it like a
  password**: anyone with this folder can read your chats.
- `*.csv`, `*.json` (except `config.json`) — exported messages
- `*.sqlite*` — copies of the WhatsApp database
- `*.txt` / `*.log` (except `requirements.txt`) — saved GUI logs
- `__pycache__/` — Python bytecode

Before pushing, run `git status` and check that none of these appear.

## Troubleshooting

| Problem | Fix |
|---|---|
| `ModuleNotFoundError: No module named 'playwright'` | The venv isn't active — run `source .venv/bin/activate`. |
| `Executable doesn't exist ... chromium` | Run `python -m playwright install chromium` inside the venv. |
| GUI window is empty/dark; terminal shows `DEPRECATION WARNING: The system version of Tk is deprecated` | The venv uses Apple's Python 3.9 with Tk 8.5. Install Homebrew Python (step 1), then `rm -rf .venv` and redo steps 3–4 with `python3.13`. |
| `ModuleNotFoundError: No module named '_tkinter'` | Install Tkinter (step 1), then recreate the venv (`rm -rf .venv`, steps 3–4). |
| QR code never appears / login times out | Run without `--headless`, or raise `--login-timeout`. |
| `Could not read WhatsApp's internal data` | WhatsApp Web changed internally; see the README section on rediscovering module names. |
