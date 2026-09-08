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

## iCalendar (.ics) Event & Task Generation (RFC 5545 / RFC 7986)
- **Commands:** `/calendar create`, `/calendar create_task`, `/calendar export_reminders`, `!ical "Title" "When" [Location]`, `!ical export`
- **AI Tools:** `create_ical_event`, `create_ical_todo`, `export_reminders_ical`
- **Description:** Generates RFC 5545 and RFC 7986 compliant `.ics` calendar invite files (`VEVENT`) and to-do/checklist task files (`VTODO`) for 1-click import into Apple Calendar, Apple Reminders, Google Calendar, and Microsoft Outlook / To-Do.
- **Features:**
  - **Events (`VEVENT`):** Supports timed and all-day events, timezone conversions with embedded `VTIMEZONE`, recurring events (`RRULE`), reminder popups (`VALARM` with 15-minute default for timed events), attendee RSVPs, organizer info, and video conference URLs (`CONFERENCE`).
  - **To-Do / Tasks (`VTODO`):** Supports action items with due dates, start times, completion status (`NEEDS-ACTION`, `COMPLETED`), percent complete, priority levels (1=High, 5=Medium, 9=Low), and reminder alarms.
  - **Reminders Export:** Dual-mode export of PocketBase reminders either as calendar events or as checklists for Apple Reminders / Microsoft To-Do.

## Reading Pacing Schedule Generator
- **Commands:** `/calendar reading_plan`
- **AI Tools:** `generate_reading_plan`
- **Description:** Computes an optimized, day-by-day reading schedule for any book and outputs a complete `.ics` calendar schedule (or task checklist) with page ranges and milestones.
- **Features:**
  - **Flexible Pacing:** Supports deadlines (target completion date), durations (e.g. 20 days), daily page budgets (e.g. 25 pages/day), and day filters (every day, weekdays only, weekends only).
  - **Progress Continuity:** Automatically offsets from current progress (e.g. "I'm on page 120, schedule the remaining pages").
  - **Milestone Checkpoints:** Automatically calculates 25%, 50%, 75%, and 100% completion dates with visual progress bars in event descriptions.
  - **Dual Output Format:** Exports either as daily calendar timeblocks (`VEVENT` with 15-minute reminder alarms) or as checklist tasks (`VTODO` for Apple Reminders and Microsoft To-Do).
## Batch Database Reminders
- **AI Tools:** `batch_set_reminders`
- **Description:** Allows scheduling multiple reminders directly into the PocketBase database in a single tool call.
- **Features:**
  - **Reading Schedule Integration:** Follow up on any generated reading plan by saying *"Schedule reminders for this reading plan in the database"*, and Shisho persists each session into PocketBase with one call.
  - **Atomic In-Memory Scheduling:** Resolves user timezone, computes UTC timestamps, validates against past dates, persists records to PocketBase, and arms in-memory timer tasks immediately.
  - **Multi-Task Support:** Schedules up to 100 reminders at once for study routines, chore lists, or project milestones.
