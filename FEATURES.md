# Possible Features

## Website Cache & Public API Integration
- **Command:** `!bookinfo <ISBN or Title>`
- **Description:** First checks your personal website's cache (`amosquet/personal-website`) for books you already plan to read. If it's a new book, it gracefully falls back to an open API (like Open Library) so you can preview the synopsis and cover in Discord.
- **Workflow:** This allows you to preview *any* book. If you like it and use `!addbook`, the bot can then save that new metadata directly into your self-hosted website cache.

## PocketBase Note-Taking & Quotes
- **Command:** `!quote "The text..." - Page 42`
- **Description:** Quickly log reading notes and favorite quotes directly into a new collection in your self-hosted PocketBase. Keeps all your reading data local and entirely under your control, unlike relying on Notion or Evernote.

## Shisho Web Dashboard
- **Description:** Expand the lightweight web server you were considering for the Suggestion Form into a full self-hosted local dashboard.
- **Features:** View your reading stats, active reminders, PocketBase book suggestions, and manage your bot's whitelist from a private web interface hosted entirely on your own machine.

## Local Focus / Pomodoro Timer
- **Command:** `!focus 25`
- **Description:** A simple, completely offline focus timer that runs in the bot's memory and DMs you when it's time to take a break. No external services required.

## Author / Series Tracking
- **Description:** A feature that tracks your favorite authors or series based on your reading history and notifies you via DM when a new book is coming out soon.
- **Workflow:** Runs a weekly background task that checks an API like Google Books or OpenLibrary for upcoming releases related to authors in your `reading.json`.

## Personal Notes & Media
- **Commands:** `!note <text> [attachment]` to add, `!notes` to list.
- **Description:** A quick way to save personal thoughts, ideas, or media (images, links). 
- **Storage:** Notes are saved in a dedicated `notes` collection in your self-hosted PocketBase database for easy retrieval and management.

## Summary / Review Generation
- **Description:** Whenever you mark a book as `read`, the bot automatically DMs you asking if you'd like to write a quick review, summary, or final thoughts.
- **Workflow:** Your responses are saved alongside the book's entry in your database, allowing you to build a personal catalog of book reviews over time.

## AI Book Concierge
- **Commands:** `!recommend <prompt>` or `!ask <question>`
- **Description:** Integrates an LLM (like Gemini or Claude) to provide highly specific book recommendations based on natural language queries (e.g., "I want a fast-paced sci-fi book similar to Project Hail Mary, but shorter").
- **Workflow:** The AI provides a curated list, and you can instantly add any of the suggested books to your reading list.

## General AI Chat
- **Command:** `!gemini <prompt>` or `/gemini`
- **Description:** A simple passthrough command to send general prompts and questions to the Gemini API right from within Discord. 
- **Workflow:** Useful as a quick assistant without having to open the browser, easily accessible by you and any friends you whitelist.

## Anki Flashcards & Spaced Repetition (.apkg)
- **Commands:** `/flashcards create`, `/flashcards from_note`, `!flashcards <prompt>`
- **Description:** Generate structured Anki flashcards (`.apkg` format) and Obsidian Spaced Repetition markdown notes from uploaded PDFs, documents, Obsidian vault notes, or prompts.
- **Workflow:** Gemini extracts key facts and definitions into atomic flashcards, builds a custom-styled `.apkg` package using `genanki`, attaches it to Discord for instant download/import into Anki, and optionally writes a spaced repetition markdown note to your Obsidian vault.

## Obsidian Vault Integration & Vault Printing
- **AI Tools:** `vault_read_note`, `vault_write_note`, `vault_patch_note`, `vault_append_note`, `vault_search`, `vault_list_files`, `vault_delete_note`, `vault_move_note`, `vault_get_backlinks`
- **Printing:** Direct printing from your local Obsidian vault to the physical printer via PocketBase Realtime queue or email fallback (e.g. "can you print my biology lecture note from today?", "print note X from my vault").
- **Permissions:** Whitelist management via `!whitelist add vault <user_id>` / `!whitelist remove vault <user_id>`, `WHITELIST_VAULT=...` in `.env`, or owner bypass via `OWNER_ID`.


