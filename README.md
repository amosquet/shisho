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

### Remote Control (Email/SMS)

I can control Shisho remotely by sending commands via email or SMS (using email-to-SMS gateways). It securely processes commands only from my allowed email addresses.

## Setup

1. **Tokens:** I keep my `DISCORD_TOKEN`, `GITHUB_TOKEN`, `SENTRY_DSN`, `POCKETBASE`, and `EMAIL_` variables in a private `.env` file.
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
- Suggestions are synced with **PocketBase**, allowing them to be seamlessly managed alongside my web-based book suggestion form.

### Book Information Lookup (Public)

`/bookinfo <query>`

- Looks up a book's details by title or ISBN.
- Returns the book's cover image, author, synopsis, page count, and average rating.
- Uses a local JSON cache to prevent duplicate API calls for the same book.

### Reminders

Set custom reminders using natural language times. The bot will send you a Direct Message when it's time! (And an email if you're the owner).

- `/remind <when> <text> [timezone]`: Sets a reminder. Timezone is optional and defaults to Eastern Time. Supports abbreviations like `jp`, `fr`, `ca`, `il`. Example: `/remind in 5 minutes Take out the trash jp`
- `/reminders`: Lists your active, upcoming reminders.

### Notifications (Public)

Whenever someone mentions me in a server Shisho is in, the bot will send me a DM with the message content and a link to jump to that message.

- **Automatic:** No command needed, just mention me!
- **Contextual:** Includes the server, channel, and a direct link to the message.

### Hot-Reloading (Owner Only)

Shisho supports hot-reloading, which means I can update its code and apply changes without restarting the bot.

- `!reload [extension]`: Reloads a specific cog (e.g., `!reload admin`) or all of them (`!reload all`).
- `!load <extension>`: Loads a new extension.
- `!unload <extension>`: Unloads an extension.

### Managing Friends (Owner Only)

- `!whitelist add <plugin> <user_id>`: Adds a user to a plugin's whitelist.
- `!whitelist remove <plugin> <user_id>`: Removes a user from a plugin's whitelist.
- `!showwhitelist <plugin>`: Shows the current whitelist for a plugin.

### Bot Updates (Owner Only)

- `!update`: Pulls the latest code from GitHub and restarts the bot service.

### Remote Email/SMS Commands (Owner Only)

- I can send an email (or SMS via carrier gateway) to the bot's configured address to run commands remotely.
- Supported commands: `!addbook`, `!suggest`, `!suggestions`, `!reminders`, `!remind`, `!bookinfo`, and `!ping`.

---
