# Secret Santa Discord Bot

A Discord bot for one Secret Santa at a time in each server. It collects private wishlists and addresses, draws a derangement that respects exclusions, and lets pairs message each other without sharing names in the channel.

Prefix commands use `s!`. A space after the mark is fine, and `S!` works the same, so `S! tutorial` and `s!tutorial` are the same command. Mentioning the bot works as that same prefix, so `@SecretSanta join` is `s!join`. The same actions are available as slash commands after the bot syncs them.

Anything else after the mention is a joke, not a command. `@SecretSanta who's on your naughty list?` gets a short fictional reply. A bare `@SecretSanta` gets a canned line and does not call the model. The joke path does not look up events, matches, wishlists, or addresses. Real commands keep doing that.

## Features

- One open event per server, with signup, locked, drawn, and done phases
- An organizer who runs the event and is not in the draw unless they join
- Wishlists, do-not-gift notes, notes, an optional spending limit, and an optional shipping address
- Digital gifts that do not require an address
- A past-match menu so you can skip someone you were paired with before, even if they have not joined yet, and a redraw when someone leaves after the draw
- Anonymous messages with emoji, GIF links, attached pictures, and a Reply button. `s!notes` DMs the latest note in each direction again
- Problem reports and feature requests. The organizer reads them in a DM with `s!reports`. A one-time what's-new note announces new features
- A command tutorial when someone asks, and a short hint the first time they use a related command
- Gift progress: bought, shipped, received. A tracking link can go with the shipped step and is DMed to the recipient
- Reminder settings the organizer edits in a form. The bot checks once a day and sends after the chosen number of days, weekly by default.
- A short welcome DM when someone joins, plus grouped help and an event guide
- A public joke when someone @mentions the bot with anything that is not a real command. The reply is made up. It never reads the draw, wishlists, or addresses.

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
CLOUDFLARE_ACCOUNT_ID=your_cloudflare_account_id
CLOUDFLARE_API_TOKEN=your_workers_ai_read_token
EASTER_EGG_MODEL=@cf/ibm-granite/granite-4.0-h-micro
```

`CLOUDFLARE_API_TOKEN` is a Workers AI Read token. Leave it unset and @mention jokes stay on the canned lines. `EASTER_EGG_MODEL` is optional. The default is `@cf/ibm-granite/granite-4.0-h-micro`. `@cf/google/gemma-4-26b-a4b-it` is the fuller joke if Granite is too thin. Do not point this at a deprecated model.

## Running the Bot

From `secret_santa_bot`:

```bash
poetry run python src/bot.py
```

The process listens on `0.0.0.0:8080`. `/` and `/health` return 200 when the database answers and 503 when it does not. The port opens before the schema migration so a deploy health check can pass while the old container releases its database lock.

## Commands

Server management (organizer or a server admin):

- `s!create [name]` - Create an event and open the setup guide. The organizer is not enrolled.
- `s!setup` - Open that guide again: signup date, gift deadline, spending cap, and rules. The form shows the saved lines and writes only the ones you change. Block a match on that guide locks two people the same private way as `s!block`.
- `s!announce #channel` - Post in that channel that signup is open and how to join. The setup guide has the same channel menu.
- `s!lock` / `s!unlock` - Close or reopen signup.
- `s!deadline YYYY-MM-DD` - Set the gift deadline.
- `s!signupby YYYY-MM-DD` - Set the last day people can join. `s!signupby clear` removes it. After that day, signup closes.
- `s!waiting` - DM the organizer who has not shipped. The list has names only, not their match.
- `s!carrylocks` - Copy organizer locks from the last finished event in this server. `s!unblock` drops one.
- `s!start` - Draw matches and DM them. No preview is sent.
- `s!reveal reveal` - DM the organizer the full draw. This breaks anonymity.
- `s!finish` - Archive a drawn event so another can be created.
- `s!cancel` - Delete this server's open event.
- `s!remove @member` - Remove someone. After the draw, the seat is redrawn.
- `s!block @person @person` - Keep two people from being matched. The names are sent by DM, the command message is removed, and the channel only says a DM was sent. The setup guide's Block a match button does the same thing. They cannot remove this. If the draw already paired them, it changes as few matches as it can. `s!unblock` removes the lock. `s!blocks` DMs the list.
- `s!cap <text>` - Set a spending cap for the event. It shows on a match when that person has not set their own. `s!cap clear` removes it.
- `s!rules <text>` - Add a short note to the welcome DM and the match. `s!rules clear` removes it.
- `s!settings` - Choose profile and shipping reminders, the days between them, and how early shipping reminders start.
- `s!remind` - DM people who still need a wishlist or address right now.
- `s!broadcast <message>` - DM every participant.
- `s!reports` - DM the saved problem reports and feature requests for this server. This still works after the event is finished.
- `s!logs` - DM the latest log lines.
- `s!ratelimit on|off|commands <n>|window <seconds>|status`

Participants:

- `s!join` / `s!leave` - Joining sends a short welcome DM: fill in your profile, plus any rules or past matches. Joining again in the same event says you're already in and does not send that DM. `s!help` groups the commands, and `s!info` shows the full guide. `s!tutorial` is there when someone asks.
- `s!profile` - Private form for wishlist, notes, address, and the digital-gift toggle. The Delivery window button sets when a package can arrive.
- `s!setwishlist <text>` / `s!setaddress <text>` / `s!setbudget <text>` - In a server channel, the command message is deleted. The spending limit is optional.
- `s!setwindow <text>` - Optional. Tell your giver when a package can arrive, or when you are away. `s!setwindow clear` removes it. In a server channel, the command message is deleted.
- `s!myinfo` - DM your profile.
- `s!exclude @member` (also `s!skip`) / `s!unexclude @member` (also `s!unskip` and `s!include`) - Skip someone you were paired with before. They do not have to have joined. `/profile` in the server has the same menu. In a DM, the bot points you back to that menu.
- `s!match` and `s!partnerinfo` - DM your match again.
- `s!message gifter <text>` / `s!message giftee <text>` - Emoji and GIF links stay in the note. A direct GIF or image link, or a picture attached to a prefix command, shows in the note.
- `s!notes` - DM the latest anonymous note in each direction again. Names stay out of the copy.
- `s!gift bought` / `s!gift shipped [link]` / `s!gift received` / `s!gift tracking <link>` - The link is sent to the recipient when the gift is marked shipped, or later with `tracking`.
- `s!participants` / `s!status` / `s!info`
- `s!problem <text>` / `s!idea <text>` - Save a problem report or a feature request. The note is stored and written to the log.
- `s!news` - Show what has been added. The same note appears once on its own the next time someone uses the bot, then only when something new ships.
- `s!tutorial` - Walk through the commands one page at a time. It also arrives the first time someone joins. Back and Next move between the pages, and it starts again whenever someone asks.
- `s!hint` - List the hints, or show one again with `s!hint profile`. This works anytime.
- Hints also appear once after a related command. Joining mentions the profile. Messaging mentions emoji and GIFs. A what's-new note takes that turn instead of a hint.

`s!match`, `s!message`, `s!myinfo`, `s!profile`, and `s!status` work in a DM when you are in exactly one open event.

## Event flow

1. An organizer creates the event with `s!create` and follows the setup guide. `s!announce #channel` tells that channel how to join.
2. Participants join, get a DM explaining the event, and fill in a wishlist. The first time they join, the tutorial arrives too. A shipping address is required unless the gift is digital.
3. Optional: set the last day to join with `s!signupby`, lock signup, set a gift deadline, copy last year's locks with `s!carrylocks`, skip past matches, and set reminder timing with `s!settings`.
4. `s!start` writes a draw only when every profile is complete and a valid derangement exists. `s!start anyway` draws when wishlists are still empty, then DMs the organizer and every participant, including anyone who still needs a wishlist. An address is still required unless the gift is digital. If one is missing, the reply says an address is still required and does not draw.
5. Each person is DMed their recipient. The organizer does not receive the full list.
6. Pairs can message anonymously and mark the gift bought, shipped, and received.
7. `s!finish` archives the event. `s!cancel` deletes the open event in that server only.

## Tests

```bash
poetry run pytest
poetry run pytest --cov
```

`tests/test_smoke.py` walks one event through every command against Postgres. Database tests use `TEST_DATABASE_URL` when it is set. Otherwise they start a temporary local Postgres cluster.

## Setting up Discord

1. Create an application in the [Discord Developer Portal](https://discord.com/developers/applications).
2. Create a bot and copy the token into `.env`.
3. Enable the Message Content and Server Members intents.
4. Invite the bot with the `bot` and `applications.commands` scopes and permission to send and read messages.

## Notes

- Finished events stay in the database. Cancelling deletes only the open event in that server.
- Anonymous message logs record the direction and whether delivery worked. They do not record display names.
- `s!problem` and `s!idea` are stored and written to the log as `PROBLEM` or `FEATURE`. `s!reports` DMs that list to the organizer.
- A spending limit is optional and is shown to the giver with the wishlist. An event spending cap fills in when someone leaves their own blank. `s!rules` is included in the welcome DM and on the match.
- The organizer can lock two people apart. That lock stays even if they try to remove it. The latest anonymous note in each direction can be DMed again with `s!notes`. A tracking link saved on the shipped step is DMed to the recipient.
- The what's-new note is shown once per person. `s!news` shows it again. A newer note appears only after the changelog version changes.
- Each hint is stored per person, so a restart does not show the same hint again. `s!tutorial` can be opened any time.
- A wishlist edited after the draw is sent to the giver.
- Leaving after the draw fails, and changes nothing, when the remaining people cannot be redrawn.

## License

[MIT License](LICENSE)
