# Secret Santa Discord Bot

A Discord bot for one Secret Santa at a time in each server. It collects private wishlists and addresses, draws a derangement that respects exclusions, and lets pairs message each other without sharing names in the channel.

Prefix commands use `s!`. The same actions are available as slash commands after the bot syncs them.

## Features

- One open event per server, with signup, locked, drawn, and done phases
- An organizer who runs the event and is not in the draw unless they join
- Wishlists, do-not-gift notes, notes, and an optional shipping address
- Digital gifts that do not require an address
- A past-match menu so you can skip someone you were paired with before, even if they have not joined yet, and a redraw when someone leaves after the draw
- Anonymous messages with emoji, GIF links, attached pictures, and a Reply button
- Problem reports and feature requests, plus a one-time what's-new note
- A command tutorial when someone asks, and a short hint the first time they use a related command
- Gift progress: bought, shipped, received
- Reminder settings the organizer edits in a form. The bot checks once a day and sends after the chosen number of days, weekly by default.
- A welcome DM when someone joins, plus grouped help and an event guide

## Prerequisites

- Python 3.10 or higher
- Poetry
- PostgreSQL (`DATABASE_URL`)
- A Discord bot token

## Installation

1. Install Poetry if you do not already have it:

```bash
pipx install poetry
```

2. Clone the repository and install dependencies:

```bash
git clone <repository-url>
cd secret_santa_bot
poetry install
```

3. Create a `.env` file in `secret_santa_bot`:

```env
DISCORD_TOKEN=your_discord_bot_token_here
DATABASE_URL=postgresql://user:password@host:5432/secret_santa
```

## Running the Bot

From `secret_santa_bot`:

```bash
poetry run python src/bot.py
```

The process listens on `0.0.0.0:8080`. `/` and `/health` return 200 when the database answers and 503 when it does not. The port opens before the schema migration so a deploy health check can pass while the old container releases its database lock.

## Commands

Server management (organizer or a server admin):

- `s!create [name]` - Create an event. The organizer is not enrolled.
- `s!lock` / `s!unlock` - Close or reopen signup.
- `s!deadline YYYY-MM-DD` - Set the shipping deadline.
- `s!start` - Draw matches and DM them. No preview is sent.
- `s!reveal reveal` - DM the organizer the full draw. This breaks anonymity.
- `s!finish` - Archive a drawn event so another can be created.
- `s!cancel` - Delete this server's open event.
- `s!remove @member` - Remove someone. After the draw, the seat is redrawn.
- `s!settings` - Choose profile and shipping reminders, the days between them, and how early shipping reminders start.
- `s!remind` - DM people who still need a wishlist or address right now.
- `s!broadcast <message>` - DM every participant.
- `s!logs` - DM the latest log lines.
- `s!ratelimit on|off|commands <n>|window <seconds>|status`

Participants:

- `s!join` / `s!leave` - Joining sends a DM that explains the event. `s!help` groups the commands, and `s!info` shows the same guide.
- `s!profile` - Private form for wishlist, notes, address, and the digital-gift toggle.
- `s!setwishlist <text>` / `s!setaddress <text>` - In a server channel, the command message is deleted.
- `s!myinfo` - DM your profile.
- `s!exclude @member` (also `s!skip`) / `s!unexclude @member` (also `s!unskip` and `s!include`) - Skip someone you were paired with before. They do not have to have joined. `/profile` in the server has the same menu. In a DM, the bot points you back to that menu.
- `s!match` and `s!partnerinfo` - DM your match again.
- `s!message gifter <text>` / `s!message giftee <text>` - Emoji and GIF links stay in the note. A direct GIF or image link, or a picture attached to a prefix command, shows in the note.
- `s!gift bought|shipped|received`
- `s!participants` / `s!status` / `s!info`
- `s!problem <text>` / `s!idea <text>` - Save a problem report or a feature request. The note is stored and written to the log.
- `s!news` - Show what has been added. The same note appears once on its own the next time someone uses the bot, then only when something new ships.
- `s!tutorial` - Walk through the commands one page at a time. It starts only when someone asks. Back and Next move between the pages.
- Hints appear once, and only after a related command. Joining mentions the profile. Messaging mentions emoji and GIFs. A what's-new note takes that turn instead of a hint.

`s!match`, `s!message`, `s!myinfo`, `s!profile`, and `s!status` work in a DM when you are in exactly one open event.

## Event flow

1. An organizer creates the event with `s!create`.
2. Participants join, get a DM explaining the event, and fill in a wishlist. A shipping address is required unless the gift is digital.
3. Optional: lock signup, set a deadline, skip past matches, and set reminder timing with `s!settings`.
4. `s!start` writes a draw only when every profile is complete and a valid derangement exists.
5. Each person is DMed their recipient. The organizer does not receive the full list.
6. Pairs can message anonymously and mark the gift bought, shipped, and received.
7. `s!finish` archives the event. `s!cancel` deletes the open event in that server only.

## Tests

```bash
poetry run pytest
```

Database tests use `TEST_DATABASE_URL` when it is set. Otherwise they start a temporary local Postgres cluster.

## Setting up Discord

1. Create an application in the [Discord Developer Portal](https://discord.com/developers/applications).
2. Create a bot and copy the token into `.env`.
3. Enable the Message Content and Server Members intents.
4. Invite the bot with the `bot` and `applications.commands` scopes and permission to send and read messages.

## Notes

- Finished events stay in the database. Cancelling deletes only the open event in that server.
- Anonymous message logs record the direction and whether delivery worked. They do not record display names.
- `s!problem` and `s!idea` are stored and written to the log as `PROBLEM` or `FEATURE`, so they can be found without a crash.
- The what's-new note is shown once per person. `s!news` shows it again. A newer note appears only after the changelog version changes.
- Each hint is stored per person, so a restart does not show the same hint again. `s!tutorial` can be opened any time.
- A wishlist edited after the draw is sent to the giver.
- Leaving after the draw fails, and changes nothing, when the remaining people cannot be redrawn.

## License

[MIT License](LICENSE)
