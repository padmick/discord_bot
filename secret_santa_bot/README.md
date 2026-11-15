# Secret Santa Discord Bot

A Discord bot for one Secret Santa at a time in each server. It collects private wishlists and addresses, draws a derangement that respects exclusions, and lets pairs message each other without sharing names in the channel.

Prefix commands use `s!`. The same actions are available as slash commands after the bot syncs them.

## Features

- One open event per server, with signup, locked, drawn, and done phases
- An organizer who runs the event and is not in the draw unless they join
- Wishlists, do-not-gift notes, shirt size, notes, and an optional shipping address
- Digital gifts that do not require an address
- Mutual exclusions and a redraw when someone leaves after the draw
- Anonymous messages with a Reply button
- Gift progress: bought, shipped, received
- Daily reminders for missing profiles and for gifts that are not shipped near the deadline

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

The process listens on port 8080 at `/health`. The check fails when the database cannot be reached.

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
- `s!remind` - DM people who still need a wishlist or address.
- `s!broadcast <message>` - DM every participant.
- `s!logs` - DM the latest log lines.
- `s!ratelimit on|off|commands <n>|window <seconds>|status`

Participants:

- `s!join` / `s!leave`
- `s!profile` - Private form for wishlist, notes, shirt size, address, and the digital-gift toggle.
- `s!setwishlist <text>` / `s!setaddress <text>` - In a server channel, the command message is deleted.
- `s!myinfo` - DM your profile.
- `s!exclude @member` / `s!unexclude @member` - Keep two people from being assigned either way.
- `s!match` and `s!partnerinfo` - DM your match again.
- `s!message gifter <text>` / `s!message giftee <text>`
- `s!gift bought|shipped|received`
- `s!participants` / `s!status` / `s!info`

`s!match`, `s!message`, `s!myinfo`, and `s!profile` work in a DM when you are in exactly one open event.

## Event flow

1. An organizer creates the event with `s!create`.
2. Participants join and fill in a wishlist. A shipping address is required unless the gift is digital.
3. Optional: lock signup, set a deadline, and add exclusions.
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
- A wishlist edited after the draw is sent to the giver.
- Leaving after the draw fails, and changes nothing, when the remaining people cannot be redrawn.

## License

[MIT License](LICENSE)
