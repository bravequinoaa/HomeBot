# HomeBot

A modular Discord bot. The first module converts uploaded weekly schedule images or PDFs into `.ics` calendar files that can be imported directly into any calendar app (Google Calendar, Apple Calendar, Outlook, etc.).

---

## How It Works (The Agentic Part)

Traditional software follows rigid rules: if input matches pattern X, do Y. That breaks immediately with a real-world schedule because people write times a hundred different ways — `630`, `6:30am`, `half past six`, `before lunch` — and no regex or parser can cover all of them.

This project is **agentic** because the Claude API is used as the decision-making core of the pipeline. Rather than writing brittle parsing rules, we hand Claude an image or PDF and ask it to *reason* about what it sees — the same way a person would:

1. **Perception** — Claude's vision and document capabilities let it read images and PDFs directly, including handwritten or oddly-formatted text.
2. **Reasoning under ambiguity** — Claude interprets fuzzy time formats, infers AM/PM from context (e.g., a block labeled `10-11:30` after morning events is clearly AM), and makes judgment calls about unclear text.
3. **Structured output** — Claude returns clean JSON that the rest of the code (date anchoring, `.ics` building) can act on deterministically.

Claude acts as the **perception + reasoning layer** between raw, unstructured human input and the structured data the system needs. Without it, you would need to write and maintain an enormous amount of brittle parsing logic that would still fail on edge cases.

### Single-turn vs. Full Agentic

This project uses Claude in a **single-turn reasoning** pattern — one prompt in, one JSON response out. That is the foundation of agentic AI. More advanced agentic systems build on this by adding:

- **Tool use** — The agent calls functions (look up today's date, query a database, send a follow-up message) as part of its reasoning loop.
- **Multi-step planning** — The agent breaks a task into steps, executes them in sequence, and checks results along the way.
- **Memory** — The agent recalls context from previous interactions.

This bot could be extended in those directions — for example, Claude could ask a clarifying question when a time is genuinely ambiguous, or remember a user's typical schedule format. For now, single-turn reasoning is the right level of complexity.

---

## Project Structure

```
HomeBot/
├── main.py                   # Bot entry point — loads all cogs
├── cogs/
│   └── schedule/             # Schedule-to-ICS module
│       ├── __init__.py
│       ├── cog.py            # Discord event listener
│       ├── parser.py         # Claude API + date anchoring
│       └── ics_builder.py    # .ics file generation
├── requirements.txt
├── .env.example
└── .gitignore
```

To add a new feature, create a new folder under `cogs/` and add one line to `main.py`.

---

## Setup

### 1. Clone and install dependencies

```bash
git clone <repo_url> HomeBot
cd HomeBot
python3 -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure secrets

```bash
cp .env.example .env
```

Edit `.env` and fill in:

```
DISCORD_BOT_TOKEN=your_discord_bot_token
ANTHROPIC_API_KEY=your_anthropic_api_key
```

### 3. Enable Discord intents

In the [Discord Developer Portal](https://discord.com/developers/applications):

- Select your application → **Bot**
- Under **Privileged Gateway Intents**, enable **Message Content Intent**

### 4. Run

```bash
python main.py
```

---

## Usage

Upload a schedule image (JPG, PNG, WebP, GIF) or PDF to any channel the bot can read. The bot will:

1. React with ⏳ while processing
2. Reply with a `.ics` file named `{START_DATE}-{END_DATE}.ics` (e.g., `2026-03-23-2026-03-29.ics`)
3. React with ✅ on success or ❌ with an error message on failure

Import the `.ics` file into your calendar app of choice.

### Supported file types

| Type | Extensions |
|------|-----------|
| Images | `.jpg`, `.jpeg`, `.png`, `.gif`, `.webp` |
| Documents | `.pdf` |

### Date handling

- If the schedule includes explicit dates (e.g., "March 24"), those are used directly.
- If the schedule only has day names (e.g., "Monday–Sunday"), events are anchored to the **upcoming Monday** from the date of upload.

---

## VPS Deployment (systemd)

Create `/etc/systemd/system/homebot.service`:

```ini
[Unit]
Description=HomeBot Discord Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=homebot
WorkingDirectory=/home/homebot/HomeBot
EnvironmentFile=/home/homebot/HomeBot/.env
ExecStart=/home/homebot/HomeBot/venv/bin/python main.py
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now homebot
sudo journalctl -u homebot -f   # follow logs
```

---

## Adding New Cogs

1. Create `cogs/<feature>/` with an `__init__.py` that exposes `async def setup(bot)`
2. Add `"cogs.<feature>"` to the `COGS` list in `main.py`
