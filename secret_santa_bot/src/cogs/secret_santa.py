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
        help_text += "s!setaddress - Set your delivery address\n"
        help_text += "s!myinfo - View your current wishlist and address\n"
        help_text += "s!partnerinfo - View your partner's gift preferences\n"
        help_text += "s!participants - List all participants\n"
        help_text += "s!message - Send a message to your Secret Santa partner\n"
        help_text += "s!start - Start the Secret Santa event\n"
        help_text += "s!cancel - Cancel the Secret Santa event\n"
        help_text += "s!remind - Send reminders to participants with missing info\n"
        help_text += "s!info - Display bot information"
        
        # Split into multiple messages if too long
        if len(help_text) > 1900:  # Leave room for Discord markdown
            messages = [help_text[i:i+1900] for i in range(0, len(help_text), 1900)]
            for msg in messages:
                await self.get_destination().send(msg)
        else:
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
            f"🎄 {ctx.author.name[:50]} has started a Secret Santa event! 🎅\n\n"
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
        """Set your wishlist"""
        if len(wishlist) > 1000:  # Setting a reasonable limit for wishlists
            await ctx.send("❌ Wishlist is too long! Please keep it under 1000 characters.")
            return
            
        user_id = str(ctx.author.id)
        try:
            self.db_manager.set_wishlist(user_id, wishlist)
            log_event("WISHLIST", f"{ctx.author.name} set their wishlist")
            await ctx.send("✅ Wishlist set successfully!")
            
            # Check if this completes their required info
            missing_info = self.db_manager.check_missing_info()
            user_missing = next((user for user in missing_info if user['user_id'] == user_id), None)
            if user_missing:
                if user_missing['missing_address']:
                    await ctx.send("ℹ️ Don't forget to set your address using `s!setaddress`!")
            else:
                await ctx.send("🎄 Great! You've completed all required information!")
        except Exception as e:
            await ctx.send("❌ Error setting wishlist. Please try again with a shorter wishlist.")
            print(f"Error setting wishlist: {e}")

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
        participant_list = '\n'.join([f"- {p['name'][:50]}" for p in participants])
        
        if len(participant_list) > 1900:
            chunks = [participant_list[i:i+1900] for i in range(0, len(participant_list), 1900)]
            for i, chunk in enumerate(chunks, 1):
                await ctx.send(f"Participants (Part {i}/{len(chunks)}):\n{chunk}")
        else:
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
        # Check if user is admin or creator
        is_admin = isinstance(ctx.author, discord.Member) and ctx.author.guild_permissions.administrator
        is_creator = self.db_manager.is_creator_or_admin(str(ctx.author.id))
        
        if not (is_admin or is_creator):
            await ctx.send("❌ Only the event creator or server administrators can start the Secret Santa!")
            return
            
        participants = self.db_manager.get_all_participants()
        if len(participants) < 2:
            await ctx.send("At least two participants are required.")
            return
        
        # Check for missing information
        missing_info = self.db_manager.check_missing_info()
        if missing_info:
            await ctx.send("Cannot start - some users haven't set their preferences!")
            
            # Create a detailed missing info message
            missing_details = "**Participants missing information:**\n"
            for user in missing_info:
                member = self.bot.get_user(int(user['user_id']))
                if member:
                    missing_items = []
                    if user['missing_wishlist']:
                        missing_items.append("wishlist")
                    if user['missing_address']:
                        missing_items.append("address")
                    missing_details += f"• {member.name}: Missing {' and '.join(missing_items)}\n"
            
            missing_details += "\nUse `s!remind` to send reminders to these participants."
            await ctx.author.send(missing_details)
            
            # DM users with missing information
            for user in missing_info:
                await self._send_missing_info_reminder(user)
            return
        
        # Continue with existing start logic...
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
        
        log_event("START", f"Secret Santa started by {ctx.author.name} in server {ctx.guild.id}")
        await ctx.send("🎅 Secret Santa has begun! All participants have received their matches via DM!")

    async def _send_missing_info_reminder(self, user):
        """Helper method to send missing info reminders"""
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

    @commands.command(name='remind')
    async def remind_missing_info(self, ctx):
        """Send reminders to participants with missing information"""
        # Check if user is admin or creator
        is_admin = isinstance(ctx.author, discord.Member) and ctx.author.guild_permissions.administrator
        is_creator = self.db_manager.is_creator_or_admin(str(ctx.author.id))
        
        if not (is_admin or is_creator):
            await ctx.send("❌ Only the event creator or server administrators can send reminders!")
            return
            
        missing_info = self.db_manager.check_missing_info()
        if not missing_info:
            await ctx.send("✅ All participants have completed their information!")
            return
            
        reminder_sent = 0
        for user in missing_info:
            member = self.bot.get_user(int(user['user_id']))
            if member:
                await self._send_missing_info_reminder(user)
                reminder_sent += 1
        
        await ctx.send(f"📬 Sent reminders to {reminder_sent} participant(s) with missing information.")
        log_event("REMIND", f"Reminders sent by {ctx.author.name} to {reminder_sent} participants")

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
        if len(address) > 1000:  # Setting a reasonable limit for addresses
            await ctx.send("❌ Address is too long! Please keep it under 1000 characters.")
            return
            
        user_id = str(ctx.author.id)
        try:
            self.db_manager.set_address(user_id, address)
            log_event("ADDRESS", f"{ctx.author.name} set their address")
            await ctx.send("✅ Address set successfully! (Only your Secret Santa will see this)")
            
            # Check if this completes their required info
            missing_info = self.db_manager.check_missing_info()
            user_missing = next((user for user in missing_info if user['user_id'] == user_id), None)
            if user_missing:
                if user_missing['missing_wishlist']:
                    await ctx.send("ℹ️ Don't forget to set your wishlist using `s!setwishlist`!")
            else:
                await ctx.send("🎄 Great! You've completed all required information!")
        except Exception as e:
            await ctx.send("❌ Error setting address. Please try again with a shorter address.")
            print(f"Error setting address: {e}")

    @commands.command(name='myinfo')
    async def view_my_info(self, ctx):
        """View your current Secret Santa information"""
        user_id = str(ctx.author.id)
        
        # Get user's current info from database
        self.db_manager.cursor.execute("""
            SELECT wishlist, address
            FROM participants
            WHERE user_id = ?
        """, (user_id,))
        result = self.db_manager.cursor.fetchone()
        
        if not result:
            await ctx.send("❌ You haven't joined the Secret Santa yet! Use `s!join` to participate.")
            return
            
        wishlist, address = result
        
        # Create info message
        info_msg = (
            "🎄 **Your Secret Santa Information:**\n\n"
            f"**Wishlist:** {wishlist or 'Not set yet - use s!setwishlist'}\n"
            f"**Address:** {address or 'Not set yet - use s!setaddress'}\n\n"
            "Remember: Both wishlist and address are required before the event can start!"
        )
        
        # Send as DM
        try:
            await ctx.author.send(info_msg)
            if isinstance(ctx.channel, discord.TextChannel):
                await ctx.send("📬 I've sent your information in a DM!")
        except discord.Forbidden:
            await ctx.send("❌ I couldn't send you a DM! Please check your privacy settings.")

    @commands.command(name='match')
    async def get_match_info(self, ctx):
        """Get your Secret Santa match information again"""
        user_id = str(ctx.author.id)
        
        # Get receiver information
        receiver_id = self.db_manager.get_giftee_for_user(user_id)
        if not receiver_id:
            await ctx.send("❌ You don't have a Secret Santa match yet! Wait for the event to start.")
            return
            
        # Get receiver's details
        self.db_manager.cursor.execute("""
            SELECT name, wishlist, address
            FROM participants
            WHERE user_id = ?
        """, (receiver_id,))
        result = self.db_manager.cursor.fetchone()
        
        if result:
            name, wishlist, address = result
            match_msg = (
                f"🎄 Your Secret Santa match:\n\n"
                f"**Recipient:** {name}\n"
                f"**Wishlist:** {wishlist or 'No wishlist set'}\n"
                f"**Delivery Address:** {address or 'No address set'}\n\n"
                "You can message them anonymously using `s!message giftee <your message>`"
            )
            
            # Send as DM
            try:
                await ctx.author.send(match_msg)
                if isinstance(ctx.channel, discord.TextChannel):
                    await ctx.send("📬 I've sent your match information in a DM!")
            except discord.Forbidden:
                await ctx.send("❌ I couldn't send you a DM! Please check your privacy settings.")
        else:
            await ctx.send("❌ Error retrieving match information. Please contact an administrator.")

async def setup(bot):
    await bot.add_cog(SecretSantaCog(bot))
    print("SecretSanta cog loaded successfully!")
