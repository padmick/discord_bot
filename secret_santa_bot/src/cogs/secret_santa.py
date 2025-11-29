import discord
from discord.ext import commands
from utils import log_event
import asyncio
import time
from collections import defaultdict

# Rate limiting configuration
RATE_LIMIT_COMMANDS = 5  # Max commands per window
RATE_LIMIT_WINDOW = 30   # Window in seconds

class CustomHelpCommand(commands.DefaultHelpCommand):
    async def send_bot_help(self, mapping):
        help_text = "Available commands:\n"
        help_text += "s!create - Create a new Secret Santa event\n"
        help_text += "s!join - Join the Secret Santa event\n"
        help_text += "s!setwishlist - Set your gift preferences\n"
        help_text += "s!setaddress - Set your delivery address\n"
        help_text += "s!myinfo - View your current wishlist and address\n"
        help_text += "s!match - Get your Secret Santa match information again\n"
        help_text += "s!participants - List all participants\n"
        help_text += "s!message - Send a message to your Secret Santa partner\n"
        help_text += "s!start - Start the Secret Santa event\n"
        help_text += "s!cancel - Cancel the Secret Santa event\n"
        help_text += "s!remind - Send reminders to participants with missing info\n"
        help_text += "s!info - Display bot information\n"
        help_text += "s!broadcast - Send a message to all participants (admin only)\n"
        help_text += "s!remove - Remove a participant from the event (admin only)\n"
        help_text += "s!ratelimit - Set rate limiting for commands (admin only)\n"
        help_text += "s!logs - View recent log entries (admin only)\n"
        
        # Split into multiple messages if too long
        if len(help_text) > 1900:
            messages = [help_text[i:i+1900] for i in range(0, len(help_text), 1900)]
            for msg in messages:
                await self.get_destination().send(msg)
        else:
            await self.get_destination().send(help_text)

class SecretSantaCog(commands.Cog):
    def __init__(self, bot, db_manager):
        self.bot = bot
        self.db_manager = db_manager
        # Rate limiting: {user_id: [timestamp1, timestamp2, ...]}
        self.user_command_history = defaultdict(list)
        self.rate_limit_enabled = True
        self.rate_limit_commands = RATE_LIMIT_COMMANDS
        self.rate_limit_window = RATE_LIMIT_WINDOW

    def _check_rate_limit(self, user_id: str) -> tuple[bool, int]:
        """Check if user is rate limited. Returns (is_limited, seconds_remaining)"""
        if not self.rate_limit_enabled:
            return False, 0
        
        current_time = time.time()
        # Clean old entries
        self.user_command_history[user_id] = [
            t for t in self.user_command_history[user_id] 
            if current_time - t < self.rate_limit_window
        ]
        
        # Check if over limit
        if len(self.user_command_history[user_id]) >= self.rate_limit_commands:
            oldest = min(self.user_command_history[user_id])
            seconds_remaining = int(self.rate_limit_window - (current_time - oldest))
            return True, max(1, seconds_remaining)
        
        # Record this command
        self.user_command_history[user_id].append(current_time)
        return False, 0

    async def cog_before_invoke(self, ctx):
        """Called before every command - check rate limit"""
        # Skip rate limit for admins/creators
        is_admin = isinstance(ctx.author, discord.Member) and ctx.author.guild_permissions.administrator
        if is_admin:
            return
        
        is_limited, seconds = self._check_rate_limit(str(ctx.author.id))
        if is_limited:
            await ctx.send(f"⏳ Slow down! You're sending commands too fast. Try again in {seconds} seconds.")
            raise commands.CommandError("Rate limited")

    async def _show_potential_matches(self, ctx, pairings) -> bool:
        """Show potential matches to creator and get confirmation"""
        match_msg = "🎄 **Potential Secret Santa Matches:**\n\n"
        for pairing in pairings:
            giver = self.bot.get_user(int(pairing['giver']))
            receiver = self.bot.get_user(int(pairing['receiver']))
            if giver and receiver:
                match_msg += f"• {giver.name} → {receiver.name}\n"
        
        match_msg += "\nAre you happy with these matches? (yes/no)"
        
        try:
            await ctx.author.send(match_msg)
            if isinstance(ctx.channel, discord.TextChannel):
                await ctx.send("📬 I've sent you the potential matches in a DM!")
        except discord.Forbidden:
            await ctx.send("❌ I couldn't send you a DM! Please check your privacy settings.")
            return False

        def check(m):
            return m.author == ctx.author and m.channel == ctx.author.dm_channel and m.content.lower() in ['yes', 'no']

        try:
            response = await self.bot.wait_for('message', timeout=300.0, check=check)
            return response.content.lower() == 'yes'
        except asyncio.TimeoutError:
            await ctx.author.send("❌ No response received within 5 minutes. Please run s!start again.")
            return False

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
        # Check if there's an active event
        if not self.db_manager.is_event_active():
            await ctx.send("❌ There is no active Secret Santa event! Ask an admin to create one with `s!create`")
            return
            
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

        # Keep generating matches until creator approves
        matches_approved = False
        while not matches_approved:
            pairings = self.db_manager.assign_partners(participants)
            matches_approved = await self._show_potential_matches(ctx, pairings)
            if not matches_approved:
                await ctx.author.send("🔄 Generating new matches...")

        # Send out match notifications
        for pairing in pairings:
            giver = self.bot.get_user(int(pairing['giver']))
            receiver = self.bot.get_user(int(pairing['receiver']))
            
            if giver and receiver:
                try:
                    await self._send_match_notification(
                        giver, 
                        receiver, 
                        pairing['receiver_wishlist'],
                        pairing['receiver_address']
                    )
                except discord.Forbidden:
                    await ctx.send(f"❌ Couldn't send match notification to {giver.name}. Please check DM permissions.")
        
        log_event("START", f"Secret Santa started by {ctx.author.name} in server {ctx.guild.id}")
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

    @commands.command(name='message')
    async def send_anonymous_message(self, ctx, recipient_type: str, *, message: str):
        """Send an anonymous message to your Secret Santa partner"""
        if not self.db_manager.is_event_active():
            await ctx.send("❌ There is no active Secret Santa event!")
            return
            
        if len(message) > 1000:
            await ctx.send("❌ Message too long! Please keep it under 1000 characters.")
            return
            
        user_id = str(ctx.author.id)
        
        if recipient_type.lower() == 'gifter':
            # Get the user's gifter (person giving them a gift)
            gifter_id = self.db_manager.get_gifter_for_user(user_id)
            if not gifter_id:
                await ctx.send("❌ You don't have a Secret Santa assigned yet!")
                return
                
            gifter = self.bot.get_user(int(gifter_id))
            if gifter:
                formatted_msg = await self._format_message_notification(message, False)
                success = await self._send_dm_with_log(
                    gifter, 
                    formatted_msg,
                    f"Giftee({ctx.author.name})", 
                    "TO_GIFTER"
                )
                if success:
                    await ctx.send("✉️ Message sent to your Secret Santa!")
                else:
                    await ctx.send("❌ Couldn't send message - your Secret Santa has DMs disabled")
            
        elif recipient_type.lower() == 'giftee':
            # Get the user's giftee (person they're giving a gift to)
            giftee_id = self.db_manager.get_giftee_for_user(user_id)
            if not giftee_id:
                await ctx.send("❌ You don't have a gift recipient assigned yet!")
                return
                
            giftee = self.bot.get_user(int(giftee_id))
            if giftee:
                formatted_msg = await self._format_message_notification(message, True)
                success = await self._send_dm_with_log(
                    giftee, 
                    formatted_msg,
                    f"Gifter({ctx.author.name})", 
                    "TO_GIFTEE"
                )
                if success:
                    await ctx.send("✉️ Message sent to your giftee!")
                else:
                    await ctx.send("❌ Couldn't send message - your giftee has DMs disabled")
        else:
            await ctx.send("❌ Invalid recipient! Use `gifter` to message your Secret Santa or `giftee` to message your recipient.")

    @commands.command(name='setwishlist')
    async def set_wishlist(self, ctx, *, wishlist):
        """Set your wishlist"""
        if len(wishlist) > 1000:
            await ctx.send("❌ Wishlist is too long! Please keep it under 1000 characters.\nTip: Consider using bullet points for better organization.")
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
                    await ctx.send("️ Don't forget to set your address using `s!setaddress`!")
            else:
                await ctx.send("🎄 Great! You've completed all required information!")
        except Exception as e:
            await ctx.send("❌ Error setting wishlist. Please try again with a shorter wishlist.")
            print(f"Error setting wishlist: {e}")

    @commands.command(name='setaddress')
    async def set_address(self, ctx, *, address):
        """Set your address for gift delivery"""
        if len(address) > 1000:
            await ctx.send("❌ Address is too long! Please keep it under 1000 characters.\nTip: Only include essential delivery information.")
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
        self.db_manager._safe_execute("""
            SELECT wishlist, address
            FROM participants
            WHERE user_id = %s
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
        self.db_manager._safe_execute("""
            SELECT name, wishlist, address
            FROM participants
            WHERE user_id = %s
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

    @commands.command(name='participants')
    async def list_participants(self, ctx):
        """List all participants in the Secret Santa event"""
        if not self.db_manager.is_event_active():
            await ctx.send("❌ There is no active Secret Santa event!")
            return
            
        participants = self.db_manager.get_all_participants()
        if not participants:
            await ctx.send("No participants have joined yet!")
            return
            
        participant_list = "🎄 **Current Participants:**\n"
        for i, p in enumerate(participants, 1):
            participant_list += f"{i}. {p['name']}\n"
            
        # Split message if too long
        if len(participant_list) > 1900:
            chunks = [participant_list[i:i+1900] for i in range(0, len(participant_list), 1900)]
            for i, chunk in enumerate(chunks, 1):
                await ctx.send(f"Page {i}/{len(chunks)}:\n{chunk}")
        else:
            await ctx.send(participant_list)

    @commands.command(name='logs')
    async def view_logs(self, ctx):
        """Display last 10 lines from the log file (Admin/Creator only)"""
        # Check if user is admin or creator
        is_admin = isinstance(ctx.author, discord.Member) and ctx.author.guild_permissions.administrator
        is_creator = self.db_manager.is_creator_or_admin(str(ctx.author.id))
        
        if not (is_admin or is_creator):
            await ctx.send("❌ Only the event creator or server administrators can use this command!")
            return

        try:
            with open('secret_santa.log', 'r') as log_file:
                # Get last 10 lines
                lines = log_file.readlines()
                last_logs = lines[-10:] if len(lines) >= 10 else lines
                
                log_text = "📋 **Last Log Entries:**\n\n"
                log_text += ''.join(last_logs)
                
                # Send logs via DM
                try:
                    await ctx.author.send(log_text)
                    await ctx.send("📬 Debug information has been sent to your DMs!")
                except discord.Forbidden:
                    await ctx.send("❌ Couldn't send DM. Please check your privacy settings.")
                    
        except FileNotFoundError:
            await ctx.send("❌ Log file not found!")
        except Exception as e:
            await ctx.send(f"❌ Error reading logs: {str(e)}")

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
                reminder_sent += 1
        
        await ctx.send(f"📬 Sent reminders to {reminder_sent} participant(s) with missing information.")
        log_event("REMIND", f"Reminders sent by {ctx.author.name} to {reminder_sent} participants")

    @commands.command(name='broadcast')
    async def broadcast_message(self, ctx, *, message: str):
        """Send a message to all participants (Admin/Creator only)"""
        # Check if user is admin or creator
        is_admin = isinstance(ctx.author, discord.Member) and ctx.author.guild_permissions.administrator
        is_creator = self.db_manager.is_creator_or_admin(str(ctx.author.id))
        
        if not (is_admin or is_creator):
            await ctx.send("❌ Only the event creator or server administrators can broadcast messages!")
            return
            
        if len(message) > 1900:
            await ctx.send("❌ Message too long! Please keep it under 1900 characters.")
            return
            
        participants = self.db_manager.get_all_participants()
        if not participants:
            await ctx.send("❌ No participants to send messages to!")
            return
            
        broadcast_msg = (
            "📢 **Announcement from Secret Santa Admin:**\n\n"
            f"{message}"
        )
        
        success_count = 0
        failed_count = 0
        
        for participant in participants:
            member = self.bot.get_user(int(participant['user_id']))
            if member:
                try:
                    await member.send(broadcast_msg)
                    success_count += 1
                    await self._log_message_sent(
                        ctx.author.name,
                        member.name,
                        "BROADCAST",
                        True
                    )
                except discord.Forbidden:
                    failed_count += 1
                    await self._log_message_sent(
                        ctx.author.name,
                        member.name,
                        "BROADCAST",
                        False
                    )
        
        status_msg = (
            f"📬 Broadcast sent to {success_count} participant(s)\n"
            f"❌ Failed to send to {failed_count} participant(s)"
        )
        await ctx.send(status_msg)
        log_event("BROADCAST", f"Message broadcast by {ctx.author.name} to {success_count} participants")

    @commands.command(name='remove')
    async def remove_participant(self, ctx, *, user_identifier: str):
        """Remove a participant from the Secret Santa event (Admin/Creator only)"""
        # Check if user is admin or creator
        is_admin = isinstance(ctx.author, discord.Member) and ctx.author.guild_permissions.administrator
        is_creator = self.db_manager.is_creator_or_admin(str(ctx.author.id))
        
        if not (is_admin or is_creator):
            await ctx.send("❌ Only the event creator or server administrators can remove participants!")
            return
        
        if not self.db_manager.is_event_active():
            await ctx.send("❌ There is no active Secret Santa event!")
            return
        
        # Try to find user by mention, ID, or name
        target_user_id = None
        target_name = None
        
        # Check if it's a mention
        if ctx.message.mentions:
            target_user = ctx.message.mentions[0]
            target_user_id = str(target_user.id)
            target_name = target_user.name
        # Check if it's a user ID
        elif user_identifier.isdigit():
            target_user_id = user_identifier
            user = self.bot.get_user(int(user_identifier))
            target_name = user.name if user else f"User {user_identifier}"
        # Try to find by name
        else:
            participant = self.db_manager.get_participant_by_name(user_identifier)
            if participant:
                target_user_id = participant['user_id']
                target_name = participant['name']
            else:
                await ctx.send(f"❌ Could not find participant matching '{user_identifier}'")
                return
        
        # Check if trying to remove the creator
        if self.db_manager.is_creator_or_admin(target_user_id):
            await ctx.send("❌ Cannot remove the event creator! Use `s!cancel` to cancel the entire event instead.")
            return
        
        # Remove the participant
        success = self.db_manager.remove_participant(target_user_id)
        
        if success:
            log_event("REMOVE", f"{target_name} was removed from Secret Santa by {ctx.author.name}")
            await ctx.send(f"✅ {target_name} has been removed from the Secret Santa event.")
            
            # Try to notify the removed user
            try:
                removed_user = self.bot.get_user(int(target_user_id))
                if removed_user:
                    await removed_user.send(
                        "ℹ️ You have been removed from the Secret Santa event by an administrator.\n"
                        "If you believe this was a mistake, please contact the event organizer."
                    )
            except discord.Forbidden:
                pass  # Can't DM the user, that's okay
        else:
            await ctx.send(f"❌ {target_name} is not a participant in the Secret Santa event.")

    @commands.command(name='ratelimit')
    async def set_rate_limit(self, ctx, action: str, value: int = None):
        """Configure rate limiting for bot commands (Admin/Creator only)
        
        Usage:
        s!ratelimit on - Enable rate limiting
        s!ratelimit off - Disable rate limiting
        s!ratelimit commands 10 - Set max commands per window
        s!ratelimit window 60 - Set window duration in seconds
        s!ratelimit status - Show current settings
        """
        # Check if user is admin or creator
        is_admin = isinstance(ctx.author, discord.Member) and ctx.author.guild_permissions.administrator
        is_creator = self.db_manager.is_creator_or_admin(str(ctx.author.id))
        
        if not (is_admin or is_creator):
            await ctx.send("❌ Only the event creator or server administrators can configure rate limiting!")
            return
        
        action = action.lower()
        
        if action == 'on':
            self.rate_limit_enabled = True
            await ctx.send("✅ Rate limiting enabled!")
            log_event("RATELIMIT", f"Rate limiting enabled by {ctx.author.name}")
            
        elif action == 'off':
            self.rate_limit_enabled = False
            await ctx.send("✅ Rate limiting disabled!")
            log_event("RATELIMIT", f"Rate limiting disabled by {ctx.author.name}")
            
        elif action == 'commands':
            if value is None or value < 1:
                await ctx.send("❌ Please provide a valid number of commands (minimum 1)")
                return
            self.rate_limit_commands = value
            await ctx.send(f"✅ Rate limit set to {value} commands per {self.rate_limit_window} seconds")
            log_event("RATELIMIT", f"Rate limit commands set to {value} by {ctx.author.name}")
            
        elif action == 'window':
            if value is None or value < 5:
                await ctx.send("❌ Please provide a valid window duration (minimum 5 seconds)")
                return
            self.rate_limit_window = value
            await ctx.send(f"✅ Rate limit window set to {value} seconds")
            log_event("RATELIMIT", f"Rate limit window set to {value}s by {ctx.author.name}")
            
        elif action == 'status':
            status = "enabled" if self.rate_limit_enabled else "disabled"
            await ctx.send(
                f"⚙️ **Rate Limit Settings:**\n"
                f"Status: {status}\n"
                f"Max commands: {self.rate_limit_commands}\n"
                f"Window: {self.rate_limit_window} seconds\n\n"
                f"Users can send {self.rate_limit_commands} commands every {self.rate_limit_window} seconds."
            )
        else:
            await ctx.send(
                "❌ Invalid action! Use:\n"
                "`s!ratelimit on` - Enable rate limiting\n"
                "`s!ratelimit off` - Disable rate limiting\n"
                "`s!ratelimit commands <number>` - Set max commands\n"
                "`s!ratelimit window <seconds>` - Set time window\n"
                "`s!ratelimit status` - Show current settings"
            )

    async def _send_match_notification(self, giver, receiver, wishlist, address):
        """Helper method to send match notification, handling long messages"""
        intro_msg = (
            f"🎄 Your Secret Santa match:\n\n"
            f"**Recipient:** {receiver.name}\n"
        )
        await giver.send(intro_msg)

        # Send wishlist (potentially split)
        wishlist_msg = f"**Wishlist:**\n{wishlist or 'No wishlist set'}"
        if len(wishlist_msg) > 1900:
            chunks = [wishlist_msg[i:i+1900] for i in range(0, len(wishlist_msg), 1900)]
            for i, chunk in enumerate(chunks, 1):
                await giver.send(f"Wishlist (Part {i}/{len(chunks)}):\n{chunk}")
        else:
            await giver.send(wishlist_msg)

        # Send address (potentially split)
        address_msg = f"**Delivery Address:**\n{address or 'No address set'}"
        if len(address_msg) > 1900:
            chunks = [address_msg[i:i+1900] for i in range(0, len(address_msg), 1900)]
            for i, chunk in enumerate(chunks, 1):
                await giver.send(f"Address (Part {i}/{len(chunks)}):\n{chunk}")
        else:
            await giver.send(address_msg)

        # Send final instructions
        await giver.send("You can message them anonymously using `s!message giftee <your message>`")

    async def _format_message_notification(self, message: str, is_from_gifter: bool) -> str:
        """Format message notification with helpful reply instructions"""
        prefix = "🎁 Your Secret Santa sent you a message:" if is_from_gifter else "🎄 Your giftee sent you a message:"
        reply_cmd = "s!message gifter" if is_from_gifter else "s!message giftee"
        
        return (
            f"{prefix}\n\n"
            f"{message}\n\n"
            f"To reply, use: `{reply_cmd} <your message>`"
        )

    async def _log_message_sent(self, sender: str, recipient: str, message_type: str, success: bool):
        """Log message sending attempts and results"""
        status = "✅" if success else "❌"
        log_event("MESSAGE", 
            f"{status} {message_type} message: {sender} → {recipient}")

    async def _send_dm_with_log(self, user: discord.User, message: str, 
                              sender_name: str, message_type: str) -> bool:
        """Send DM and log the attempt"""
        try:
            await user.send(message)
            await self._log_message_sent(sender_name, user.name, message_type, True)
            return True
        except discord.Forbidden:
            await self._log_message_sent(sender_name, user.name, message_type, False)
            return False

async def setup(bot):
    await bot.add_cog(SecretSantaCog(bot))
    print("SecretSanta cog loaded successfully!")
