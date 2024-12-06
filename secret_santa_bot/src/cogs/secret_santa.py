import discord
from discord.ext import commands
from utils import log_event
import asyncio

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
        user_id = str(ctx.author.id)
        
        if recipient_type.lower() == 'gifter':
            gifter_id = self.db_manager.get_gifter_for_user(user_id)
            if not gifter_id:
                await ctx.send("❌ You don't have a Secret Santa assigned yet!")
                return
                
            gifter = self.bot.get_user(int(gifter_id))
            if gifter:
                formatted_msg = await self._format_message_notification(message, True)
                success = await self._send_dm_with_log(
                    gifter, 
                    formatted_msg,
                    f"Giftee({ctx.author.name})", 
                    "TO_GIFTER"
                )
                if success:
                    await ctx.send("✉️ Message sent to your Secret Santa!")
                else:
                    await ctx.send("❌ Couldn't send message - recipient has DMs disabled")
                
        elif recipient_type.lower() == 'giftee':
            giftee_id = self.db_manager.get_giftee_for_user(user_id)
            if not giftee_id:
                await ctx.send("❌ You don't have a gift recipient assigned yet!")
                return
                
            giftee = self.bot.get_user(int(giftee_id))
            if giftee:
                formatted_msg = await self._format_message_notification(message, False)
                success = await self._send_dm_with_log(
                    giftee, 
                    formatted_msg,
                    f"Gifter({ctx.author.name})", 
                    "TO_GIFTEE"
                )
                if success:
                    await ctx.send("✉️ Message sent to your giftee!")
                else:
                    await ctx.send("❌ Couldn't send message - recipient has DMs disabled")

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

    @commands.command(name='debug')
    async def debug_info(self, ctx):
        """Display all participant information (Admin/Creator only)"""
        # Check if user is admin or creator
        is_admin = isinstance(ctx.author, discord.Member) and ctx.author.guild_permissions.administrator
        is_creator = self.db_manager.is_creator_or_admin(str(ctx.author.id))
        
        if not (is_admin or is_creator):
            await ctx.send("❌ Only the event creator or server administrators can use this command!")
            return
            
        # Get all participant information
        self.db_manager.cursor.execute("""
            SELECT name, wishlist, address
            FROM participants
        """)
        results = self.db_manager.cursor.fetchall()
        
        if not results:
            await ctx.send("❌ No participants found in the database.")
            return
            
        # Create debug message
        debug_msg = "🔍 **Secret Santa Debug Information:**\n\n"
        for name, wishlist, address in results:
            debug_msg += f"**Participant:** {name}\n"
            debug_msg += f"**Wishlist:** {wishlist or 'Not set'}\n"
            debug_msg += f"**Address:** {address or 'Not set'}\n"
            debug_msg += "-" * 40 + "\n"
        
        # Split message if too long
        if len(debug_msg) > 1900:
            chunks = [debug_msg[i:i+1900] for i in range(0, len(debug_msg), 1900)]
            for chunk in chunks:
                await ctx.author.send(chunk)
        else:
            await ctx.author.send(debug_msg)
            
        if isinstance(ctx.channel, discord.TextChannel):
            await ctx.send("📬 Debug information has been sent to your DMs!")
            
        log_event("DEBUG", f"Debug information accessed by {ctx.author.name}")

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
