# Shisho (ししょ)

Named after the Japanese word for "Librarian," **Shisho** is my personal Discord bot designed to help me manage my reading list and other stuff easily within discord.

It’s built to be modular, so I can keep adding random features as I need them.

## What can it do?

### Reading List Manager

I can add books directly to my website's `reading.json` on GitHub.

- **Top-heavy:** New books are automatically inserted at the top of my list.
- **Smart Dating:** It handles start/end dates for me based on whether I'm currently reading or just finished a book.
- **Customisable:** I can still manually enter old dates if I'm catching up on entries.

### Access Control

Since I want to let friends use it for certain features, like maybe a public recommendations list or something, I have a permission system:

- **Owner:** I have full control over everything.
- **Whitelists:** I can whitelist specific friends for specific plugins (cogs) either in the `.env` or directly via Discord commands.

### Health Monitoring

Integrated with **Sentry** so I know if something breaks.

## Setup

1. **Tokens:** I keep my `DISCORD_TOKEN`, `GITHUB_TOKEN`, and `SENTRY_DSN` in a private `.env` file.
2. **Environment:** `uv`.
   ```bash
   uv sync
   uv run main.py
   ```
3. **Discord Intents:** I make sure **Message Content Intent** is toggled ON in the dev portal so Shisho can actually work.

## Commands

### Managing Books

`!addbook "Project Hail Mary" "Andy Weir" "2021" "9780593135211" read`
_(Status options: read, reading, planned, dropped)_

### Suggested Books (Public)

`!suggest 9780593135211`

- Anyone can suggest books!
- You can suggest using an ISBN, or manually: `!suggest "Title" "Author"`

### Notifications (Public)

Whenever someone mentions me in a server Shisho is in, the bot will send me a DM with the message content and a link to jump to that message.

- **Automatic:** No command needed, just mention the me!
- **Contextual:** Includes the server, channel, and a direct link to the message.

### Managing Friends

`!whitelist add suggestedbooks 1234567890`

---
