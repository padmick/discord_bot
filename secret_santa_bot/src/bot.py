import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import os
import threading

import discord
from discord.ext import commands
from dotenv import load_dotenv
from flask import Flask

from cogs.secret_santa import CustomHelpCommand, RateLimited, SecretSantaCog
from database import DatabaseManager
from utils import logger, report_failure

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(
    command_prefix=commands.when_mentioned_or("s!", "S!"),
    case_insensitive=True,
    intents=intents,
    help_command=CustomHelpCommand(),
)
runtime = {"db": None, "synced": False}


def _private_command_message(command_name: str) -> str:
    if command_name in {"exclude", "unexclude", "skip", "unskip", "include"}:
        return "Open `/profile` in the server to skip someone you were paired with before."
    return "Run that command in the server."


def health_payload(database_ok: bool, bot_ready: bool) -> tuple[dict, int]:
    body = {
        "status": "healthy" if database_ok else "unhealthy",
        "bot_ready": bot_ready,
        "database": database_ok,
    }
    return body, 200 if database_ok else 503


@bot.event
async def on_ready():
    print(f"{bot.user} has connected to Discord!")
    if runtime["db"] is not None and bot.get_cog("SecretSantaCog") is None:
        bot.db_manager = runtime["db"]
        await bot.add_cog(SecretSantaCog(bot, runtime["db"]))
    elif runtime["db"] is None:
        print("Secret Santa commands disabled - no database connection")
    if runtime["db"] is not None and not runtime["synced"]:
        try:
            synced = await bot.tree.sync()
            runtime["synced"] = True
            print(f"Synced {len(synced)} slash commands")
        except Exception as exc:
            report_failure("slash command sync", exc)
    await bot.change_presence(activity=discord.Game(name="Secret Santa"))


_USAGE_ERRORS = (
    commands.CommandNotFound,
    commands.CheckFailure,
    commands.MissingRequiredArgument,
    commands.BadArgument,
)


def command_failure_where(ctx) -> str:
    command = getattr(getattr(ctx, "command", None), "qualified_name", None) or "unknown"
    guild_id = getattr(getattr(ctx, "guild", None), "id", None) or "dm"
    user_id = getattr(getattr(ctx, "author", None), "id", "unknown")
    return f"command {command} guild {guild_id} user {user_id}"


def interaction_failure_where(interaction) -> str:
    command = getattr(getattr(interaction, "command", None), "qualified_name", None) or "unknown"
    guild_id = getattr(interaction, "guild_id", None) or "dm"
    user_id = getattr(getattr(interaction, "user", None), "id", "unknown")
    return f"command {command} guild {guild_id} user {user_id}"


def record_command_problem(where: str, error: BaseException) -> None:
    """Write a command failure to stderr. Usage mistakes stay at info."""
    original = getattr(error, "original", error)
    if isinstance(error, RateLimited) or isinstance(original, RateLimited):
        return
    shown = original if isinstance(original, BaseException) else error
    if isinstance(error, _USAGE_ERRORS) or isinstance(shown, _USAGE_ERRORS):
        logger.info("%s rejected: %s: %s", where, type(shown).__name__, shown)
        return
    report_failure(where, error)


@bot.event
async def on_command_error(ctx, error):
    record_command_problem(command_failure_where(ctx), error)
    original = getattr(error, "original", error)
    if isinstance(original, RateLimited):
        return
    if isinstance(error, commands.CommandNotFound):
        await ctx.send("Command not found. Use `s!help` to see available commands.")
        return
    if isinstance(error, commands.NoPrivateMessage):
        await ctx.send(_private_command_message(getattr(ctx.command, "name", "")))
        return
    if isinstance(error, commands.CheckFailure):
        await ctx.send(str(error) or "You can't use that command here.")
        return
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("A required value is missing. Use `s!help` to see how the command works.")
        return
    if isinstance(error, commands.BadArgument):
        await ctx.send("I couldn't read one of those values. Use `s!help` if you need the command shape.")
        return
    await ctx.send(f"An error occurred: {str(original)[:900]}")


@bot.tree.error
async def on_app_command_error(interaction, error):
    record_command_problem(interaction_failure_where(interaction), error)
    original = getattr(error, "original", error)
    if isinstance(original, RateLimited):
        return
    if isinstance(error, discord.app_commands.NoPrivateMessage) or isinstance(original, commands.NoPrivateMessage):
        message = _private_command_message(getattr(interaction.command, "name", ""))
    else:
        message = str(original)[:900] or "The command failed."
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


app = Flask(__name__)


@app.route("/")
@app.route("/health")
def health_check():
    """Health check for the process and the database."""
    database = runtime["db"]
    database_ok = database.ping() if database is not None else False
    body, status = health_payload(database_ok, bot.is_ready())
    return body, status


def start_health_server():
    thread = threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=8080, debug=False, use_reloader=False),
        daemon=True,
    )
    thread.start()


def main():
    if not TOKEN:
        raise SystemExit("DISCORD_TOKEN is not set")
    # App Platform replaces the old container once TCP port 8080 accepts
    # connections. Open it before the schema migration so that migration can
    # wait for the old container to release its database lock.
    start_health_server()
    try:
        runtime["db"] = DatabaseManager()
    except Exception as exc:
        report_failure("database startup", exc)
        logger.warning("The bot will start without Secret Santa commands until the database is reachable.")
        runtime["db"] = None
    bot.db_manager = runtime["db"]
    bot.run(TOKEN)


if __name__ == "__main__":
    main()
