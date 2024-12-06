import discord
from discord.ext import commands
from dotenv import load_dotenv
import os
from database import DatabaseManager
from utils import log_event
from cogs.secret_santa import SecretSantaCog, CustomHelpCommand

# Load environment variables
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

# Set up intents
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

# Initialize bot
bot = commands.Bot(
    command_prefix=commands.when_mentioned_or('s!', 'S!'), 
    case_insensitive=True,
    intents=intents
)

# Initialize database
db_manager = DatabaseManager()

# Set up help command
bot.help_command = CustomHelpCommand()

@bot.event
async def on_ready():
    print(f'{bot.user} has connected to Discord!')
    # Add cogs here after bot is ready
    await bot.add_cog(SecretSantaCog(bot, db_manager))
    await bot.change_presence(activity=discord.Game(name="Secret Santa"))

def check_permissions(ctx):
    if isinstance(ctx.author, discord.Member):
        permissions = ctx.channel.permissions_for(ctx.author)
        can_send_messages = permissions.send_messages
        can_read_messages = permissions.read_messages
        return can_send_messages and can_read_messages
    return True

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        await ctx.send("Command not found. Use `s!help` to see available commands.")
    else:
        error_msg = str(error)[:900]
        print(f"Error: {error}")
        await ctx.send(f"❌ An error occurred: {error_msg}")

# Run bot
if __name__ == "__main__":
    bot.run(TOKEN)
