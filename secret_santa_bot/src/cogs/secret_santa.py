import discord
from discord.ext import commands
from database import DatabaseManager
from utils import log_event
from typing import Optional
import psutil

class CustomHelpCommand(commands.DefaultHelpCommand):
    async def send_bot_help(self, mapping):
        help_text = "Available commands:\n"
        help_text += "s!create - Create a new Secret Santa event\n"
        help_text += "s!join - Join the Secret Santa event\n"
        help_text += "s!setwishlist - Set your gift preferences\n"
        help_text += "s!partnerinfo - View your partner's gift preferences\n"
        help_text += "s!participants - List all participants\n"
        help_text += "s!message - Send a message to your Secret Santa partner\n"
        help_text += "s!start - Start the Secret Santa event\n"
        help_text += "s!cancel - Cancel the Secret Santa event\n"
        help_text += "s!info - Display bot information\n"
        help_text += "s!setaddress - Set your delivery address\n"
        
        await self.get_destination().send(help_text)

class SecretSantaCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db_manager = DatabaseManager()
        self.process = psutil.Process()

    @commands.command(name='info')
    async def info(self, ctx):
        """Display information about the bot"""
        guild_count = len(self.bot.guilds)
        memory_usage = round((self.process.memory_info().rss / 1024 ** 2), 2)
        await ctx.send(f"Guilds: {guild_count}\nMemory Usage: {memory_usage} MB")

    @commands.command(name='create')
    async def create_secret_santa(self, ctx):
        """Create a new Secret Santa event"""
        if self.db_manager.is_event_active():
            await ctx.send("❌ A Secret Santa event is already in progress! Cancel it first with `s!cancel`")
            return
        
        # Add creator as first participant
        self.db_manager.add_participant(str(ctx.author.id), ctx.author.name, is_creator=True)
        log_event("CREATE", f"New Secret Santa event created by {ctx.author.name} in server {ctx.guild.id}")
        
        create_msg = (
            f"🎄 {ctx.author.name} has started a Secret Santa event! 🎅\n\n"
            "To join the Secret Santa:\n"
            "1. Use `s!join` in this channel or DM the bot\n"
            "2. You'll receive instructions to set your wishlist and address\n\n"
            "Once everyone has joined and set their preferences, use `s!start` to begin!"
        )
        await ctx.send(create_msg)

    @commands.command(name='join')
    async def join_secret_santa(self, ctx):
        """Join the Secret Santa event"""
        user_id = str(ctx.author.id)
        name = ctx.author.name
        self.db_manager.add_participant(user_id, name)
        log_event("JOIN", f"{name} joined the Secret Santa event")
        await ctx.send(f"{name}, you've joined the Secret Santa!")
        
        # Send DM with instructions
        welcome_msg = (
            "Welcome to Secret Santa! 🎅\n\n"
            "Please set up your preferences:\n"
            "1. Set your wishlist with `s!setwishlist <your wishlist>`\n"
            "2. Set your address with `s!setaddress <your address>`\n\n"
            "Both are required before the event can start!"
        )
        await ctx.author.send(welcome_msg)

    @commands.command(name='setwishlist')
    async def set_wishlist(self, ctx, *, wishlist):
        """Set your gift preferences"""
        user_id = str(ctx.author.id)
        self.db_manager.set_wishlist(user_id, wishlist)
        log_event("WISHLIST", f"{ctx.author.name} set their wishlist")
        await ctx.send(f"Wishlist set for {ctx.author.name}")

    @commands.command(name='partnerinfo')
    async def get_partner_info(self, ctx):
        """View your partner's gift preferences"""
        user_id = str(ctx.author.id)
        partner_info = self.db_manager.get_partner_info(user_id)
        if partner_info:
            await ctx.send(f"Your Secret Santa is: {partner_info['name']}\nTheir wishlist: {partner_info['wishlist']}")
        else:
            await ctx.send("You don't have a partner yet!")

    @commands.command(name='participants')
    async def list_participants(self, ctx):
        """List all participants in the Secret Santa"""
        participants = self.db_manager.get_all_participants()
        participant_list = '\n'.join([f"- {p['name']}" for p in participants])
        await ctx.send(f"Participants:\n{participant_list}")

    @commands.command(name='message')
    async def send_message(self, ctx, recipient_type=None, *, message=None):
        """Send a message to your Secret Santa partner"""
        if recipient_type is None or message is None:
            usage_msg = (
                "📝 **Message Command Usage:**\n"
                "`s!message gifter <your message>` - Send a message to the person giving you a gift\n"
                "`s!message giftee <your message>` - Send a message to the person you're giving a gift to\n\n"
                "Example: `s!message giftee Thank you for the wishlist!`"
            )
            await ctx.send(usage_msg)
            return
            
        user_id = str(ctx.author.id)
        if recipient_type.lower() == 'gifter':
            partner_id = self.db_manager.get_gifter_for_user(user_id)
            anonymous_msg = f"🎄 Message from your giftee:\n\n{message}"
        elif recipient_type.lower() == 'giftee':
            partner_id = self.db_manager.get_giftee_for_user(user_id)
            anonymous_msg = f"🎅 Message from your Secret Santa:\n\n{message}"
        else:
            await ctx.send("❌ Invalid recipient type. Use 'gifter' or 'giftee'.")
            return
        
        if partner_id:
            partner = self.bot.get_user(int(partner_id))
            if partner:
                await partner.send(anonymous_msg)
                await ctx.send("✉️ Message sent successfully!")
            else:
                await ctx.send("❌ Unable to find the partner's account.")
        else:
            await ctx.send("❌ You don't have a partner yet!")

    @commands.command(name='start')
    async def start_secret_santa(self, ctx):
        """Start the Secret Santa event and assign partners"""
        participants = self.db_manager.get_all_participants()
        if len(participants) < 2:
            await ctx.send("At least two participants are required.")
            return
        
        # Check for missing information
        missing_info = self.db_manager.check_missing_info()
        if missing_info:
            await ctx.send("Cannot start - some users haven't set their preferences!")
            
            # DM users with missing information
            for user in missing_info:
                member = self.bot.get_user(int(user['user_id']))
                if member:
                    missing_items = []
                    if user['missing_wishlist']:
                        missing_items.append("wishlist (use s!setwishlist)")
                    if user['missing_address']:
                        missing_items.append("address (use s!setaddress)")
                    
                    missing_msg = (
                        "⚠️ The Secret Santa event cannot start because you haven't set your:\n"
                        f"{', '.join(missing_items)}\n"
                        "Please set these to allow the event to start!"
                    )
                    await member.send(missing_msg)
            return
        
        pairings = self.db_manager.assign_partners(participants)
        for pairing in pairings:
            giver = self.bot.get_user(int(pairing['giver']))
            receiver = self.bot.get_user(int(pairing['receiver']))
            
            if giver and receiver:
                partner_msg = (
                    f"🎄 Your Secret Santa match:\n\n"
                    f"**Recipient:** {receiver.name}\n"
                    f"**Wishlist:** {pairing['receiver_wishlist']}\n"
                    f"**Delivery Address:** {pairing['receiver_address']}\n\n"
                    "You can message them anonymously using `s!message giftee <your message>`"
                )
                await giver.send(partner_msg)
        
        log_event("START", f"Secret Santa started in server {ctx.guild.id}")
        await ctx.send("🎅 Secret Santa has begun! All participants have received their matches via DM!")

    @commands.command(name='cancel')
    async def cancel_secret_santa(self, ctx):
        """Cancel the Secret Santa event"""
        if not self.db_manager.is_event_active():
            await ctx.send("❌ There is no active Secret Santa event to cancel!")
            return
        
        # Check if user is admin or creator
        is_admin = isinstance(ctx.author, discord.Member) and ctx.author.guild_permissions.administrator
        is_creator = self.db_manager.is_creator_or_admin(str(ctx.author.id))
        
        if not (is_admin or is_creator):
            await ctx.send("❌ Only the event creator or server administrators can cancel the Secret Santa!")
            return
        
        self.db_manager.cancel_secret_santa()
        log_event("CANCEL", f"Secret Santa cancelled by {ctx.author.name} in server {ctx.guild.id}")
        await ctx.send("🎄 Secret Santa event cancelled! Use `s!create` to start a new one.")

    @commands.command(name='setaddress')
    async def set_address(self, ctx, *, address):
        """Set your address for gift delivery"""
        user_id = str(ctx.author.id)
        self.db_manager.set_address(user_id, address)
        log_event("ADDRESS", f"{ctx.author.name} set their address")
        await ctx.send("Address set successfully! (Only your Secret Santa will see this)")

async def setup(bot):
    await bot.add_cog(SecretSantaCog(bot))
    print("SecretSanta cog loaded successfully!")
