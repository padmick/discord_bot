# Secret Santa Discord Bot 🎅

A Discord bot to manage Secret Santa events in your server. Features anonymous messaging, wishlist management, and automated partner assignments.

## Features
- Create and manage Secret Santa events
- Collect wishlists and delivery addresses
- Randomly assign Secret Santa pairs
- Anonymous messaging between gifters and recipients
- Admin controls for event management

## Prerequisites
- Python 3.10 or higher
- pipx (for managing Poetry)
- Poetry (for dependency management)
- A Discord bot token

## Installation

1. Install pipx (if not already installed):

```
pipx install poetry
```

3. Clone the repository:

```bash
git clone <repository-url>
cd secret_santa_bot
```

4. Install dependencies:

```bash
poetry install
```

5. Create a `.env` file in the root directory:

```env
DISCORD_TOKEN=your_discord_bot_token_here
```

## Running the Bot

From the project directory:

```bash
poetry run python src/bot.py
```

## Commands

### Event Management
- `s!create` - Create a new Secret Santa event (admin only)
- `s!start` - Start the Secret Santa event and assign partners (admin/creator only)
- `s!cancel` - Cancel the Secret Santa event (admin/creator only)
- `s!remind` - Send reminders to participants with missing information (admin/creator only)

### Participant Commands
- `s!join` - Join the Secret Santa event
- `s!setwishlist <wishlist>` - Set your gift preferences
- `s!setaddress <address>` - Set your delivery address
- `s!partnerinfo` - View your partner's gift preferences
- `s!participants` - List all participants

### Messaging
- `s!message gifter <message>` - Send an anonymous message to your Secret Santa
- `s!message giftee <message>` - Send an anonymous message to your recipient

## Setting Up Your Own Bot

1. Create a new Discord application at [Discord Developer Portal](https://discord.com/developers/applications)
2. Create a bot for your application
3. Enable necessary intents (Message Content, Server Members)
4. Copy your bot token and add it to the `.env` file
5. Invite the bot to your server using the OAuth2 URL generator:
   - Required permissions: Send Messages, Read Messages/View Channels
   - Required scopes: bot, applications.commands

## Event Flow
1. Admin creates event with `s!create`
2. Participants join with `s!join`
3. Participants set wishlist and address
4. Admin starts event with `s!start`
5. Bot DMs everyone their Secret Santa assignment
6. Participants can message anonymously using `s!message`

## Notes
- Only one Secret Santa event can be active at a time
- Both wishlist and address are required before the event can start
- Server admins and the event creator can manage the event
- All participant information is cleared when an event is cancelled

## Troubleshooting
- If the bot doesn't respond, check if it has proper permissions
- If commands fail, ensure all required information is provided
- For DM issues, make sure your Discord privacy settings allow DMs from server members

## Contributing
Dew it fools!

## License
[MIT License](LICENSE)
```

