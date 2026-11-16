"""Discord adapter for Secret Santa commands."""

from __future__ import annotations

import asyncio
import time
from datetime import date

import discord
from discord.ext import commands, tasks

from present import (
    Card,
    announcement_card,
    anonymous_card,
    command_help_card,
    draw_waiting_card,
    help_card,
    info_card,
    match_card,
    profile_prompt_card,
    profile_reminder_card,
    removed_card,
    reveal_cards,
    shipping_reminder_card,
    to_embed,
    wishlist_update_card,
)
from services import (
    EventService,
    RateLimiter,
    anonymous_message_log,
    resolve_dm_events,
)
from utils import log_event, report_failure
from views import NotificationSettingsView, ProfileView, ReplyView


class RateLimited(commands.CommandError):
    """Stops the command after the slow-down notice.

    The bot error handler ignores this so the user only sees the one notice.
    """


class CustomHelpCommand(commands.DefaultHelpCommand):
    def __init__(self, **options):
        attrs = dict(options.get("command_attrs") or {})
        attrs.setdefault("help", "Show commands grouped by what you are doing")
        options["command_attrs"] = attrs
        super().__init__(**options)

    async def _visible(self, commands_to_list):
        visible = [command for command in commands_to_list if not command.hidden]
        filtered = await self.filter_commands(visible, sort=True)
        return [(command.name, command.short_doc) for command in filtered]

    async def send_bot_help(self, mapping):
        del mapping
        entries = await self._visible(self.context.bot.commands)
        await _send_card(self.get_destination(), help_card(entries, self.context.clean_prefix))

    async def send_cog_help(self, cog):
        entries = await self._visible(cog.get_commands())
        await _send_card(self.get_destination(), help_card(entries, self.context.clean_prefix))

    async def send_command_help(self, command):
        signature = self.get_command_signature(command)
        description = command.help or command.short_doc or "No description."
        aliases = ", ".join(command.aliases)
        footer = f"Also known as {aliases}." if aliases else "s!help lists every command."
        await _send_card(self.get_destination(), command_help_card(signature, description, footer))


class SecretSantaCog(commands.Cog):
    def __init__(self, bot, db_manager):
        self.bot = bot
        self.service = EventService(db_manager)
        self.rate_limiter = RateLimiter()
        self.bot.add_view(ReplyView("gifter"))
        self.bot.add_view(ReplyView("giftee"))
        self.daily_reminders.start()

    def cog_unload(self):
        self.daily_reminders.cancel()

    async def cog_before_invoke(self, ctx):
        if _is_admin(ctx):
            return
        limited, seconds = self.rate_limiter.check(str(ctx.author.id), time.time())
        if not limited:
            return
        await _say(ctx, f"Slow down. Try again in {seconds} seconds.")
        raise RateLimited()

    async def run(self, func, *args, **kwargs):
        return await asyncio.to_thread(func, *args, **kwargs)

    @tasks.loop(hours=24)
    async def daily_reminders(self):
        """Check once a day. Each event sends only on the organizer's interval."""
        today = date.today()
        targets = await self.run(self.service.select_reminders, today)
        marked: set[tuple[int, str]] = set()
        for target in targets:
            user = await self._user(target["user_id"])
            if user is not None:
                try:
                    if target["kind"] == "profile":
                        await user.send(embed=to_embed(profile_reminder_card(target["missing"])))
                    else:
                        await user.send(embed=to_embed(shipping_reminder_card(target["deadline"])))
                except discord.Forbidden:
                    pass
            marked.add((target["event_id"], target["kind"]))
        for event_id, kind in marked:
            await self.run(self.service.mark_reminded, event_id, kind, today)

    @daily_reminders.before_loop
    async def _wait_for_bot(self):
        await self.bot.wait_until_ready()

    @daily_reminders.error
    async def _on_reminder_error(self, error):
        report_failure("daily reminders", error)

    @commands.hybrid_command(name="create", help="Create a Secret Santa event in this server")
    @commands.guild_only()
    async def create(self, ctx, *, name: str = "Secret Santa"):
        """Create a Secret Santa event in this server."""
        result = await self.run(self.service.create_event, str(ctx.guild.id), str(ctx.author.id), name)
        if result.ok:
            log_event("CREATE", f"guild {ctx.guild.id} organizer {ctx.author.id} event {result.data['event_id']}")
        await _show(ctx, result)

    @commands.hybrid_command(name="join", help="Join the open Secret Santa and get a DM that explains it")
    @commands.guild_only()
    async def join(self, ctx):
        """Join the open Secret Santa and get a DM that explains it."""
        result = await self.run(self.service.join, str(ctx.guild.id), str(ctx.author.id), ctx.author.display_name)
        welcome = (result.data or {}).get("welcome") if result.ok else None
        delivered = await self._dm_card(str(ctx.author.id), welcome) if welcome is not None else False
        if result.ok:
            log_event("JOIN", f"guild {ctx.guild.id} user {ctx.author.id}")
        if welcome is not None and delivered:
            await _say(ctx, result.message + "\nThe guide is in your DMs.")
        else:
            await _show(ctx, result)
        if welcome is not None and not delivered:
            await _say(ctx, "I couldn't DM the welcome note, so here it is.")
            await _send_card(ctx, welcome)

    @commands.hybrid_command(name="leave", help="Leave the Secret Santa")
    @commands.guild_only()
    async def leave(self, ctx):
        """Leave the Secret Santa."""
        result = await self.run(self.service.leave, str(ctx.guild.id), str(ctx.author.id))
        await self._after_roster_change(ctx, result, "LEAVE")

    @commands.hybrid_command(name="lock", help="Close signup")
    @commands.guild_only()
    async def lock(self, ctx):
        """Close signup."""
        result = await self.run(self.service.lock, str(ctx.guild.id), str(ctx.author.id), _is_admin(ctx))
        await _show(ctx, result)

    @commands.hybrid_command(name="unlock", help="Open signup again")
    @commands.guild_only()
    async def unlock(self, ctx):
        """Open signup again."""
        result = await self.run(self.service.unlock, str(ctx.guild.id), str(ctx.author.id), _is_admin(ctx))
        await _show(ctx, result)

    @commands.hybrid_command(name="deadline", help="Set the gift deadline as YYYY-MM-DD")
    @commands.guild_only()
    async def deadline(self, ctx, day: str):
        """Set the gift deadline as YYYY-MM-DD."""
        result = await self.run(
            self.service.set_deadline,
            str(ctx.guild.id),
            str(ctx.author.id),
            _is_admin(ctx),
            day,
        )
        await _show(ctx, result)

    @commands.hybrid_command(name="start", help="Draw matches and DM them")
    @commands.guild_only()
    async def start(self, ctx):
        """Draw matches and DM them."""
        result = await self.run(self.service.start, str(ctx.guild.id), str(ctx.author.id), _is_admin(ctx))
        if not result.ok:
            await _show(ctx, result)
            for person in (result.data or {}).get("missing", []):
                await self._dm_card(person["user_id"], draw_waiting_card(person["missing"]))
            return
        failed = 0
        for note in result.data["notifications"]:
            if not await self._dm_assignment(note):
                failed += 1
        log_event("START", f"guild {ctx.guild.id} user {ctx.author.id}")
        message = result.message
        if failed:
            message += f" {failed} participant(s) could not be reached by DM."
        await _say(ctx, message)

    @commands.hybrid_command(name="finish", help="Archive the event so a new one can be created")
    @commands.guild_only()
    async def finish(self, ctx):
        """Archive the event so a new one can be created."""
        result = await self.run(self.service.finish, str(ctx.guild.id), str(ctx.author.id), _is_admin(ctx))
        await _show(ctx, result)

    @commands.hybrid_command(name="cancel", help="Delete the open event in this server")
    @commands.guild_only()
    async def cancel(self, ctx):
        """Delete the open event in this server."""
        result = await self.run(self.service.cancel, str(ctx.guild.id), str(ctx.author.id), _is_admin(ctx))
        if result.ok:
            log_event("CANCEL", f"guild {ctx.guild.id} user {ctx.author.id}")
        await _show(ctx, result)

    @commands.hybrid_command(name="status", help="Show the event phase and who is missing a profile")
    @commands.guild_only()
    async def status(self, ctx):
        """Show the event phase and who is missing a profile."""
        result = await self.run(self.service.status, str(ctx.guild.id))
        await _show(ctx, result)

    @commands.hybrid_command(name="participants", help="List participants")
    @commands.guild_only()
    async def participants(self, ctx):
        """List participants."""
        result = await self.run(self.service.roster, str(ctx.guild.id))
        await _show(ctx, result)

    @commands.hybrid_command(
        name="exclude",
        aliases=["skip"],
        help="Skip someone you were paired with before",
    )
    @commands.guild_only()
    async def exclude(self, ctx, member: discord.Member):
        """Skip someone you were paired with before."""
        if member.bot:
            await _say(ctx, "Pick a person. Bots are not in the draw.")
            return
        result = await self.run(
            self.service.exclude,
            str(ctx.guild.id),
            str(ctx.author.id),
            str(member.id),
            member.display_name,
        )
        await _show(ctx, result)

    @commands.hybrid_command(
        name="unexclude",
        aliases=["unskip"],
        help="Let a past match be paired with you again",
    )
    @commands.guild_only()
    async def unexclude(self, ctx, member: discord.Member):
        """Let a past match be paired with you again."""
        result = await self.run(
            self.service.unexclude,
            str(ctx.guild.id),
            str(ctx.author.id),
            str(member.id),
            member.display_name,
        )
        await _show(ctx, result)

    @commands.hybrid_command(name="remove", help="Remove a participant")
    @commands.guild_only()
    async def remove(self, ctx, member: discord.Member):
        """Remove a participant."""
        result = await self.run(
            self.service.remove,
            str(ctx.guild.id),
            str(ctx.author.id),
            str(member.id),
            _is_admin(ctx),
        )
        if result.ok:
            await self._dm_card(str(member.id), removed_card())
        await self._after_roster_change(ctx, result, "REMOVE")

    @commands.hybrid_command(name="profile", help="Open your profile and the past-match menu")
    async def profile(self, ctx):
        """Open your profile and the past-match menu."""
        event = await self._personal_event(ctx)
        if event is None:
            return
        info = await self.run(self.service.my_info, event["guild_id"], str(ctx.author.id))
        if not info.ok:
            await _show(ctx, info)
            return
        in_guild = ctx.guild is not None
        view = ProfileView(
            self,
            event["guild_id"],
            str(ctx.author.id),
            bool((info.data or {}).get("digital_only")),
            in_guild=in_guild,
        )
        await _send_card(
            ctx,
            profile_prompt_card(in_guild),
            view=view,
            ephemeral=ctx.interaction is not None,
        )

    @commands.hybrid_command(name="setwishlist", help="Set your wishlist")
    async def setwishlist(self, ctx, *, wishlist: str):
        """Set your wishlist."""
        await self._set_field(ctx, "wishlist", wishlist)

    @commands.hybrid_command(name="setaddress", help="Set your shipping address")
    async def setaddress(self, ctx, *, address: str):
        """Set your shipping address."""
        await self._set_field(ctx, "address", address)

    @commands.hybrid_command(name="myinfo", help="DM your profile")
    async def myinfo(self, ctx):
        """DM your profile."""
        event = await self._personal_event(ctx)
        if event is None:
            return
        result = await self.run(self.service.my_info, event["guild_id"], str(ctx.author.id))
        if not result.ok:
            await _show(ctx, result)
            return
        await self._private(ctx, result.message, card=result.card)

    @commands.hybrid_command(name="match", help="DM your match again")
    async def match(self, ctx):
        """DM your match again."""
        await self._send_match(ctx)

    @commands.hybrid_command(name="partnerinfo", help="DM your match again")
    async def partnerinfo(self, ctx):
        """DM your match again."""
        await self._send_match(ctx)

    @commands.hybrid_command(name="message", help="Send an anonymous note to your gifter or giftee")
    async def message(self, ctx, recipient_type: str, *, text: str):
        """Send an anonymous note to your gifter or giftee."""
        await _delete_invocation(ctx)
        await self._send_anonymous(ctx.author, ctx.guild, recipient_type, text, _say_ctx(ctx))

    @commands.hybrid_command(name="gift", help="Update gift progress: bought, shipped, or received")
    async def gift(self, ctx, status: str):
        """Update gift progress: bought, shipped, or received."""
        event = await self._personal_event(ctx)
        if event is None:
            return
        result = await self.run(self.service.set_gift_status, event["guild_id"], str(ctx.author.id), status)
        await _show(ctx, result)

    @commands.hybrid_command(name="settings", help="Choose which reminders send and how many days apart")
    @commands.guild_only()
    async def settings(self, ctx):
        """Choose which reminders send and how many days apart."""
        result = await self.run(
            self.service.notification_settings,
            str(ctx.guild.id),
            str(ctx.author.id),
            _is_admin(ctx),
        )
        if not result.ok:
            await _show(ctx, result)
            return
        await _show(
            ctx,
            result,
            view=NotificationSettingsView(self, str(ctx.guild.id)),
            ephemeral=ctx.interaction is not None,
        )

    @commands.hybrid_command(name="remind", help="DM participants who still need a profile")
    @commands.guild_only()
    async def remind(self, ctx):
        """DM participants who still need a profile."""
        result = await self.run(
            self.service.missing_profile_targets,
            str(ctx.guild.id),
            str(ctx.author.id),
            _is_admin(ctx),
        )
        if not result.ok or not (result.data or {}).get("users"):
            await _show(ctx, result)
            return
        sent = 0
        for person in result.data["users"]:
            if await self._dm_card(person["user_id"], profile_reminder_card(person["missing"])):
                sent += 1
        log_event("REMIND", f"guild {ctx.guild.id} user {ctx.author.id} count {sent}")
        await _say(ctx, f"**Sent reminders** to {sent} participant(s).")

    @commands.hybrid_command(name="broadcast", help="DM every participant")
    @commands.guild_only()
    async def broadcast(self, ctx, *, text: str):
        """DM every participant."""
        if len(text) > 1900:
            await _say(ctx, "Keep the announcement under 1900 characters.")
            return
        result = await self.run(
            self.service.broadcast_recipients,
            str(ctx.guild.id),
            str(ctx.author.id),
            _is_admin(ctx),
        )
        if not result.ok:
            await _show(ctx, result)
            return
        sent = 0
        failed = 0
        card = announcement_card(text)
        for user_id in result.data["user_ids"]:
            if await self._dm_card(user_id, card):
                sent += 1
            else:
                failed += 1
        log_event("BROADCAST", f"guild {ctx.guild.id} user {ctx.author.id} sent {sent} failed {failed}")
        await _say(ctx, f"**Sent the announcement** to {sent} participant(s). {failed} could not be reached.")

    @commands.hybrid_command(name="info", help="Explain how a Secret Santa event works")
    async def info(self, ctx):
        """Explain how a Secret Santa event works."""
        await _send_card(ctx, info_card(), ephemeral=ctx.interaction is not None)

    @commands.hybrid_command(name="logs", help="DM the latest log lines")
    @commands.guild_only()
    async def logs(self, ctx):
        """DM the latest log lines."""
        event = await self.run(self.service.open_event, str(ctx.guild.id))
        organizer_id = event["organizer_id"] if event else None
        if not _is_admin(ctx) and str(ctx.author.id) != organizer_id:
            await _say(ctx, "Only the organizer or a server admin can read the log.")
            return
        try:
            with open("secret_santa.log", "r", encoding="utf-8") as handle:
                lines = handle.readlines()
        except FileNotFoundError:
            await _say(ctx, "No log file yet.")
            return
        tail = "".join(lines[-10:]) or "The log is empty."
        await self._private(ctx, f"Latest log lines:\n{tail}")

    @commands.hybrid_command(name="ratelimit", help="Show or change command rate limits")
    @commands.guild_only()
    async def ratelimit(self, ctx, action: str, value: int | None = None):
        """Show or change command rate limits."""
        event = await self.run(self.service.open_event, str(ctx.guild.id))
        organizer_id = event["organizer_id"] if event else None
        if not _is_admin(ctx) and str(ctx.author.id) != organizer_id:
            await _say(ctx, "Only the organizer or a server admin can change rate limits.")
            return
        action = action.lower()
        limiter = self.rate_limiter
        if action == "on":
            limiter.enabled = True
            text = "Rate limiting is on."
        elif action == "off":
            limiter.enabled = False
            text = "Rate limiting is off."
        elif action == "commands":
            if value is None or value < 1:
                text = "Provide a command limit of at least 1."
            else:
                limiter.max_commands = value
                text = f"The limit is {value} commands every {limiter.window} seconds."
        elif action == "window":
            if value is None or value < 5:
                text = "Provide a window of at least 5 seconds."
            else:
                limiter.window = value
                text = f"The window is {value} seconds."
        elif action == "status":
            state = "on" if limiter.enabled else "off"
            text = (
                f"Rate limiting is {state}. "
                f"{limiter.max_commands} commands every {limiter.window} seconds."
            )
        else:
            text = "Use on, off, commands, window, or status."
        log_event("RATELIMIT", f"guild {ctx.guild.id} user {ctx.author.id} action {action}")
        await _say(ctx, text)

    @commands.hybrid_command(name="reveal", help="DM the full draw. Confirm with the word reveal")
    @commands.guild_only()
    async def reveal(self, ctx, confirmation: str = ""):
        """DM the full draw. Confirm with the word reveal."""
        token = confirmation.strip().lower()
        if token != "reveal":
            await _say(
                ctx,
                "This shows every match and breaks anonymity. "
                "Reply reveal within a minute, or run s!reveal reveal.",
            )
            token = await self._wait_for_reveal(ctx)
            if token != "reveal":
                await _say(ctx, "Reveal cancelled.")
                return
        result = await self.run(
            self.service.reveal,
            str(ctx.guild.id),
            str(ctx.author.id),
            _is_admin(ctx),
            token,
        )
        if not result.ok:
            await _show(ctx, result)
            return
        log_event("REVEAL", f"event {result.data['event_id']} by {ctx.author.id}")
        try:
            for card in reveal_cards(result.data["pairings"]):
                await _send_card(ctx.author, card)
        except discord.Forbidden:
            await _say(ctx, "I couldn't DM you the draw. Allow DMs from server members.")
            return
        await _say(ctx, result.message)

    async def deliver_anonymous(self, interaction: discord.Interaction, direction: str, text: str):
        async def respond(content: str):
            if interaction.response.is_done():
                await interaction.followup.send(content, ephemeral=True)
            else:
                await interaction.response.send_message(content, ephemeral=True)

        await self._send_anonymous(interaction.user, interaction.guild, direction, text, respond)

    async def notify_wishlist(self, payload: dict):
        await self._dm_card(
            payload["notify_gifter_id"],
            wishlist_update_card(payload.get("wishlist") or ""),
        )

    async def _set_field(self, ctx, field_name: str, value: str):
        await _delete_invocation(ctx)
        event = await self._personal_event(ctx)
        if event is None:
            return
        result = await self.run(
            self.service.set_text_field,
            event["guild_id"],
            str(ctx.author.id),
            field_name,
            value,
            ctx.guild is not None,
        )
        if result.ok:
            log_event(field_name.upper(), f"user {ctx.author.id}")
        if result.data and result.data.get("notify_gifter_id"):
            await self.notify_wishlist(result.data)
        await self._private(ctx, result.message)

    async def _send_match(self, ctx):
        event = await self._personal_event(ctx)
        if event is None:
            return
        result = await self.run(self.service.match_info, event["guild_id"], str(ctx.author.id))
        if not result.ok:
            await _show(ctx, result)
            return
        await self._private(ctx, result.message, reply_as="giftee", card=result.card)

    async def _send_anonymous(self, user, guild, recipient_type: str, text: str, respond):
        if guild is not None:
            event = await self.run(self.service.open_event, str(guild.id))
            if event is None:
                await respond("There is no active Secret Santa event.")
                return
        else:
            participations = await self.run(self.service.open_participations, str(user.id))
            event = resolve_dm_events(participations)
            if event is None:
                if len(participations) > 1:
                    await respond("You are in more than one Secret Santa. Run this command in the server.")
                else:
                    await respond("Run this command in the server where you joined.")
                return
        result = await self.run(
            self.service.prepare_message,
            event["guild_id"],
            str(user.id),
            recipient_type,
            text,
        )
        if not result.ok:
            await respond(result.message)
            return
        direction = result.data["direction"]
        reply_as = result.data["reply_as"]
        delivered = await self._dm_card(
            result.data["target_id"],
            anonymous_card(direction, result.data["body"], reply_as),
            reply_as=reply_as,
        )
        log_event("MESSAGE", anonymous_message_log(delivered, direction))
        if delivered:
            await respond("**Sent.** They received the anonymous note.")
        else:
            await respond("**Not delivered.** Their DMs are closed.")

    async def _after_roster_change(self, ctx, result, action: str):
        for note in (result.data or {}).get("changed", []):
            await self._dm_assignment(note)
        if result.ok:
            log_event(action, f"guild {ctx.guild.id} user {ctx.author.id}")
        await _show(ctx, result)

    async def _personal_event(self, ctx):
        event = await self._event_for(str(ctx.author.id), ctx.guild)
        if event is not None:
            return event
        if ctx.guild is not None:
            await _say(ctx, "There is no active Secret Santa event.")
            return None
        participations = await self.run(self.service.open_participations, str(ctx.author.id))
        if len(participations) > 1:
            await _say(ctx, "You are in more than one Secret Santa. Run this command in the server.")
        else:
            await _say(ctx, "Run this command in the server where you joined.")
        return None

    async def _event_for(self, user_id: str, guild):
        if guild is not None:
            return await self.run(self.service.open_event, str(guild.id))
        participations = await self.run(self.service.open_participations, user_id)
        return resolve_dm_events(participations)

    async def _dm_assignment(self, note: dict) -> bool:
        return await self._dm_card(note["giver_id"], _match_card(note), reply_as="giftee")

    async def _dm_card(self, user_id: str, card: Card, reply_as: str | None = None) -> bool:
        user = await self._user(user_id)
        if user is None:
            return False
        try:
            await _send_card(user, card, reply_as=reply_as)
            return True
        except discord.Forbidden:
            return False

    async def _private(self, ctx, text: str, reply_as: str | None = None, card: Card | None = None):
        if ctx.guild is None:
            await _deliver_private(ctx, text, reply_as, card)
            return
        try:
            await _deliver_private(ctx.author, text, reply_as, card)
        except discord.Forbidden:
            await _say(ctx, "I couldn't DM you. Allow DMs from server members.")
            return
        await _say(ctx, "**Sent in a DM.**")

    async def _user(self, user_id: str):
        try:
            snowflake = int(user_id)
        except (TypeError, ValueError):
            return None
        user = self.bot.get_user(snowflake)
        if user is not None:
            return user
        try:
            return await self.bot.fetch_user(snowflake)
        except (discord.NotFound, discord.HTTPException):
            return None

    async def _wait_for_reveal(self, ctx) -> str:
        def check(message: discord.Message) -> bool:
            return message.author == ctx.author and message.channel == ctx.channel

        try:
            reply = await self.bot.wait_for("message", timeout=60, check=check)
        except asyncio.TimeoutError:
            return ""
        return reply.content.strip().lower()


async def setup(bot):
    await bot.add_cog(SecretSantaCog(bot, bot.db_manager))


def _is_admin(ctx) -> bool:
    permissions = getattr(ctx.author, "guild_permissions", None)
    return bool(permissions and permissions.administrator)


async def _delete_invocation(ctx):
    message = getattr(ctx, "message", None)
    if ctx.guild is None or message is None:
        return
    try:
        await message.delete()
    except (discord.Forbidden, discord.NotFound, discord.HTTPException):
        pass


def _match_card(note: dict) -> Card:
    return match_card(
        {
            "name": note["receiver_name"],
            "wishlist": note.get("wishlist"),
            "address": note.get("address"),
            "do_not_gift": note.get("do_not_gift"),
            "notes": note.get("notes"),
            "digital_only": note.get("digital_only"),
        }
    )


async def _deliver_private(destination, text: str, reply_as: str | None, card: Card | None):
    if card is not None:
        await _send_card(destination, card, reply_as=reply_as)
        return
    await _send_chunks(destination, text, reply_as=reply_as)


async def _show(ctx, result, *, view=None, ephemeral: bool = False):
    if result.card is not None:
        await _send_card(ctx, result.card, view=view, ephemeral=ephemeral)
        return
    await _say(ctx, result.message, view=view, ephemeral=ephemeral)


async def _send_card(destination, card: Card, *, view=None, ephemeral: bool = False, reply_as: str | None = None):
    extra = {}
    if ephemeral:
        extra["ephemeral"] = True
    chosen = view
    if chosen is None and reply_as:
        chosen = ReplyView(reply_as)
    if chosen is not None:
        extra["view"] = chosen
    await destination.send(embed=to_embed(card), **extra)


async def _say(ctx, content: str, *, view=None, ephemeral: bool = False):
    kwargs = {}
    if view is not None:
        kwargs["view"] = view
    if ephemeral and ctx.interaction is not None:
        kwargs["ephemeral"] = True
    await _send_chunks(ctx, content, **kwargs)


def _say_ctx(ctx):
    async def respond(content: str):
        await _say(ctx, content)

    return respond


async def _send_chunks(destination, content: str, *, reply_as: str | None = None, **kwargs):
    if not content:
        content = "Done."
    chunks = [content[index:index + 1900] for index in range(0, len(content), 1900)]
    for index, chunk in enumerate(chunks):
        extra = dict(kwargs)
        if reply_as and index == len(chunks) - 1:
            extra["view"] = ReplyView(reply_as)
        await destination.send(chunk, **extra)
