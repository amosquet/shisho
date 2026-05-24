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
