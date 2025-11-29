import discord
from discord.ext import commands
from dotenv import load_dotenv
import os
import threading
import time
import subprocess
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

    # Run database setup after bot is ready (permissions should be available now)
    if os.getenv('SETUP_DATABASE') == 'true':
        print('🔧 Scheduling database setup for 10 seconds after startup...')
        threading.Thread(target=delayed_database_setup, daemon=True).start()

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

def delayed_database_setup():
    """Run database setup after a delay to ensure permissions are granted"""
    time.sleep(10)  # Wait 10 seconds for permissions to be fully granted

    try:
        print('🔧 Running delayed database setup...')
        result = subprocess.run(['python', 'setup_database.py'],
                              capture_output=True, text=True, cwd='/app')

        print('📄 Setup output:')
        if result.stdout:
            print(result.stdout)
        if result.stderr:
            print('❌ Setup errors:', result.stderr)

        if result.returncode == 0:
            print('✅ Database setup completed successfully!')
        else:
            print(f'❌ Database setup failed with exit code: {result.returncode}')

    except Exception as e:
        print(f'❌ Error running database setup: {e}')

# Start health check server in background thread
health_thread = threading.Thread(target=run_health_server, daemon=True)
health_thread.start()

# Run bot
if __name__ == "__main__":
    bot.run(TOKEN)
