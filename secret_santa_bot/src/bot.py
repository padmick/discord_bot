import discord
from discord.ext import commands
from dotenv import load_dotenv
import os
import threading
from flask import Flask
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

# Initialize database (may be None if not configured yet)
db_manager = None
try:
    db_manager = DatabaseManager()
except Exception as e:
    print(f"⚠️  Database not available yet: {e}")
    print("🔧 Bot will start without database - add database and redeploy")

# Set up help command
bot.help_command = CustomHelpCommand()

@bot.event
async def on_ready():
    print(f'{bot.user} has connected to Discord!')
    # Add cogs here after bot is ready
    if db_manager:
        await bot.add_cog(SecretSantaCog(bot, db_manager))
    else:
        print("⚠️  Secret Santa commands disabled - no database connection")
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

# Health check Flask app
app = Flask(__name__)

@app.route('/health')
def health_check():
    """Health check endpoint for DigitalOcean"""
    return {'status': 'healthy', 'bot_ready': bot.is_ready()}, 200

def run_health_server():
    """Run Flask health check server in a separate thread"""
    app.run(host='0.0.0.0', port=8080, debug=False, use_reloader=False)

# Start health check server in background thread
health_thread = threading.Thread(target=run_health_server, daemon=True)
health_thread.start()

# Run bot
if __name__ == "__main__":
    bot.run(TOKEN)
