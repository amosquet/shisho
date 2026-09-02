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

### Account & Registration (Public)

- `/register [email]`: Create and link a Shisho account directly from Discord without needing the mobile app! Generates an 8-digit secure PIN, provisions your PocketBase account, and links your Discord ID instantly. Email is optional (defaults to `<discord_id>@discord.shisho.local`).
- `/account`: View your linked Shisho account profile and registration details.
- `/resetpin`: Regenerate a new secure PIN for your Shisho account.

### Managing Books

- `/addbook [status] [title] [author] [publish_date] [isbn] [start_date] [end_date] [cover_image]`: Adds a book to the PocketBase reading list.
- `/deletebook <book>`: Removes a book from your reading list (supports title, ISBN, or interactive autocomplete).

### Book Recommendations & Suggestions (Public)

- `/suggest [title] [author] [isbn] [recipient] [message] [is_public]`: Anyone can suggest or recommend books! You can recommend a book directly to a friend with an optional note, or make a public recommendation. Automatically fetches covers and synopsis details via Google Books, and includes an interactive button for recipients to add the book to their reading list.
- `/suggestions [filter]`: Lists recommended books. Filter by `All`, `For Me` (received recommendations), `From Me` (sent recommendations), or `Public`.
- `/deletesuggestion <suggestion>`: Removes a book from the recommendations list (supports title, ISBN, or interactive autocomplete for recommendations you created or received).

### Book Information Lookup (Public)

`/bookinfo <query>`

- Looks up a book's details by title or ISBN.
- Returns the book's cover image, author, synopsis, page count, and average rating.
- Uses a local JSON cache to prevent duplicate API calls for the same book.

### Reminders

Set custom reminders using natural language times. The bot will send you a Direct Message when it's time! (And an email if you're the owner).

- `/remind <when> <text> [timezone]`: Sets a reminder. Timezone is optional and defaults to Eastern Time. Supports abbreviations like `jp`, `fr`, `ca`, `il`. Example: `/remind in 5 minutes Take out the trash jp`
- `/reminders`: Lists your active, upcoming reminders.
- `/deletereminder <reminder>`: Deletes or cancels an active reminder (supports index number like `1`, text keyword, ID, autocomplete, or `all`).

### Notes

- `/note <text> [title] [attachment...]`: Saves a personal note (with optional file/audio attachments).
- `/notes [name]`: Lists your recent notes or views a specific note in detail.
- `/deletenote <note>`: Deletes a personal note (supports title, keyword, ID, or interactive autocomplete).

### AI Assistant & Smart Reply (Gemini)

- `/ask [prompt] [image] [audio]`: Ask Shisho questions, upload images (book covers, assignments, schedules), or share audio memos.
- **Smart Reply & Mention Action**: Reply to any message in a channel or thread and tag `@Shisho` (e.g. `@Shisho add this book`, `@Shisho remind me tomorrow at 5pm`, `@Shisho save this note`, or simply `@Shisho`). Shisho will inspect the referenced message and conversation history, determine the best course of action, and execute the corresponding database tools automatically.
- `/recommend [query]`: Access the AI Book Concierge for tailored book recommendations.

### Printing (PocketBase Realtime + Email Fallback)

Send documents, notes, or snippets to your physical printer across separate networks:

- `/print [file] [note_id] [text]`: Queues a print job in PocketBase Realtime. If PocketBase is unavailable, Shisho automatically offers an interactive `[✉️ Retry via Email]` button.
- **Message Context Menu**: Right-click any message with an attachment $\rightarrow$ **Apps** $\rightarrow$ **Print Attachment**.
- **AI Smart Reply**: Tag `@Shisho print this` or `@Shisho print note [name]` to print attachments or text summaries automatically.

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

### Bot Updates & Admin (Owner Only)

- `!update`: Pulls the latest code from GitHub and restarts the bot service.
- `!announce <message>`: Creates a new announcement broadcast.
- `!sync [spec]`: Syncs slash commands globally or to current guild (`!sync`, `!sync ~`, `!sync *`, `!sync ^`).

### Remote Email/SMS Commands (Owner Only)

- I can send an email (or SMS via carrier gateway) to the bot's configured address to run commands remotely.
- Supported commands: `!addbook`, `!deletebook` / `!removebook`, `!suggest`, `!suggestions`, `!deletesuggestion`, `!reminders`, `!remind`, `!deletereminder` / `!cancelreminder`, `!notes`, `!note`, `!deletenote`, `!bookinfo`, and `!ping`.

---

