import discord
from discord.ext import commands
from dotenv import load_dotenv
import os
from database import DatabaseManager
from utils import log_event
from cogs.secret_santa import SecretSantaCog, CustomHelpCommand

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(
    command_prefix=commands.when_mentioned_or('s!', 'S!'), 
    case_insensitive=True,
    intents=intents
)
bot.help_command = CustomHelpCommand()

@bot.event
async def on_ready():
    print(f'{bot.user} has connected to Discord!')
    await bot.change_presence(activity=discord.Game(name="Secret Santa"))

def check_permissions(ctx):
    # Check if the context is in a guild
    if isinstance(ctx.author, discord.Member):
        # Check if the user has permission to send and receive messages
        permissions = ctx.channel.permissions_for(ctx.author)
        can_send_messages = permissions.send_messages
        can_read_messages = permissions.read_messages

        return can_send_messages and can_read_messages
    else:
        # If not in a guild, assume permissions are granted (e.g., in DMs)
        return True

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        await ctx.send(f"Command not found. Use `s!help` to see available commands.")
    else:
        print(f"Error: {error}")

async def setup():
    await bot.load_extension('cogs.secret_santa')

if __name__ == '__main__':
    load_dotenv()
    bot.add_check(check_permissions)
    
    @bot.event
    async def setup_hook():
        await setup()
        print("Extensions loaded")
    
    bot.run(os.getenv('DISCORD_TOKEN'))
