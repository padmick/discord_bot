"""Discord forms for profiles and anonymous replies."""

from __future__ import annotations

import discord

from present import to_embed, tutorial_card, tutorial_page_count, tutorial_page_from_footer
from utils import report_failure


async def report_component_failure(component: str, interaction, error: BaseException) -> None:
    """Log a button or form failure and tell the person the action failed."""
    report_failure(component, error)
    if interaction is None:
        return
    try:
        if interaction.response.is_done():
            await interaction.followup.send("That action failed.", ephemeral=True)
        else:
            await interaction.response.send_message("That action failed.", ephemeral=True)
    except Exception as exc:
        report_failure(f"{component} response", exc)


class TutorialButton(discord.ui.Button):
    def __init__(self, kind: str, disabled: bool):
        super().__init__(
            label="Back" if kind == "prev" else "Next",
            style=discord.ButtonStyle.secondary,
            custom_id=f"ss:tutorial:{kind}",
            disabled=disabled,
        )

    async def callback(self, interaction: discord.Interaction):
        embeds = getattr(getattr(interaction, "message", None), "embeds", None) or []
        footer = ""
        if embeds and embeds[0].footer:
            footer = embeds[0].footer.text or ""
        page = tutorial_page_from_footer(footer)
        page += 1 if (self.custom_id or "").endswith(":next") else -1
        card = tutorial_card(page)
        await interaction.response.edit_message(
            embed=to_embed(card),
            view=TutorialView(tutorial_page_from_footer(card.footer)),
        )


class TutorialView(discord.ui.View):
    def __init__(self, page: int = 0):
        super().__init__(timeout=None)
        last = tutorial_page_count() - 1
        index = min(max(int(page), 0), last)
        self.add_item(TutorialButton("prev", index <= 0))
        self.add_item(TutorialButton("next", index >= last))

    async def on_error(self, interaction, error, item, /) -> None:
        del item
        await report_component_failure("TutorialView", interaction, error)


class ReplyButton(discord.ui.Button):
    def __init__(self, direction: str):
        super().__init__(
            label="Reply",
            style=discord.ButtonStyle.primary,
            custom_id=f"ss:reply:{direction}",
        )

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("SecretSantaCog")
        if cog is None:
            await interaction.response.send_message("Secret Santa is not available right now.", ephemeral=True)
            return
        direction = (self.custom_id or "ss:reply:gifter").rsplit(":", 1)[-1]
        await interaction.response.send_modal(ReplyModal(cog, direction))


class ReplyView(discord.ui.View):
    def __init__(self, direction: str):
        super().__init__(timeout=None)
        self.add_item(ReplyButton(direction))

    async def on_error(self, interaction, error, item, /) -> None:
        del item
        await report_component_failure("ReplyView", interaction, error)


class ReplyModal(discord.ui.Modal, title="Anonymous reply"):
    body = discord.ui.TextInput(
        label="Message",
        style=discord.TextStyle.paragraph,
        max_length=1000,
        required=True,
    )

    def __init__(self, cog, direction: str):
        super().__init__()
        self.cog = cog
        self.direction = direction

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.deliver_anonymous(interaction, self.direction, str(self.body.value))

    async def on_error(self, interaction, error, /) -> None:
        await report_component_failure(f"reply form {self.direction}", interaction, error)


class ProfileModal(discord.ui.Modal, title="Secret Santa profile"):
    def __init__(self, cog, guild_id: str, user_id: str, profile: dict):
        super().__init__()
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        self.wishlist = _field("Wishlist", profile.get("wishlist"), paragraph=True, limit=1000)
        self.budget = _field("Spending limit", profile.get("budget"), limit=80)
        self.do_not_gift = _field("Do not gift", profile.get("do_not_gift"), limit=500)
        self.notes = _field("Notes", profile.get("notes"), paragraph=True, limit=500)
        self.address = _field("Address, blank if digital", profile.get("address"), paragraph=True, limit=1000)
        for item in (self.wishlist, self.budget, self.do_not_gift, self.notes, self.address):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        if str(interaction.user.id) != self.user_id:
            await interaction.response.send_message("This form belongs to someone else.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            result = await self.cog.run(
                self.cog.service.update_profile,
                self.guild_id,
                self.user_id,
                wishlist=self.wishlist.value,
                budget=self.budget.value,
                do_not_gift=self.do_not_gift.value,
                notes=self.notes.value,
                address=self.address.value,
            )
            if result.ok and result.data and result.data.get("notify_gifter_id"):
                await self.cog.notify_wishlist(result.data)
            await interaction.followup.send(result.message, ephemeral=True)
        except Exception as exc:
            report_failure(f"profile form guild {self.guild_id} user {self.user_id}", exc)
            await interaction.followup.send("The profile could not be saved.", ephemeral=True)

    async def on_error(self, interaction, error, /) -> None:
        await report_component_failure(
            f"profile form guild {self.guild_id} user {self.user_id}",
            interaction,
            error,
        )


class PastMatchSelect(discord.ui.UserSelect):
    """Server members, including people who have not joined this Secret Santa."""

    def __init__(self):
        super().__init__(
            placeholder="Someone you were paired with before",
            min_values=1,
            max_values=10,
        )

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        if not isinstance(view, ProfileView):
            await interaction.response.send_message("This menu is not available right now.", ephemeral=True)
            return
        if str(interaction.user.id) != view.user_id:
            await interaction.response.send_message(
                "This menu belongs to the person who opened the profile.",
                ephemeral=True,
            )
            return
        people = []
        for member in self.values:
            if getattr(member, "bot", False):
                continue
            people.append((str(member.id), getattr(member, "display_name", "") or ""))
        if not people:
            await interaction.response.send_message("Pick a person. Bots are not in the draw.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        result = await view.cog.run(
            view.cog.service.skip_past_matches,
            view.guild_id,
            view.user_id,
            people,
        )
        await interaction.followup.send(result.message, ephemeral=True)


class ProfileView(discord.ui.View):
    def __init__(self, cog, guild_id: str, user_id: str, digital_only: bool, in_guild: bool = False):
        super().__init__(timeout=180)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        self.digital_only = digital_only
        if in_guild:
            self.add_item(PastMatchSelect())

    async def _owner(self, interaction: discord.Interaction) -> bool:
        if str(interaction.user.id) == self.user_id:
            return True
        await interaction.response.send_message("These buttons belong to the person who opened them.", ephemeral=True)
        return False

    @discord.ui.button(label="Edit profile", style=discord.ButtonStyle.primary)
    async def edit(self, interaction: discord.Interaction, button: discord.ui.Button):
        del button
        if not await self._owner(interaction):
            return
        info = await self.cog.run(self.cog.service.my_info, self.guild_id, self.user_id)
        if not info.ok:
            await interaction.response.send_message(info.message, ephemeral=True)
            return
        await interaction.response.send_modal(ProfileModal(self.cog, self.guild_id, self.user_id, info.data or {}))

    @discord.ui.button(label="Toggle digital gift", style=discord.ButtonStyle.secondary)
    async def toggle_digital(self, interaction: discord.Interaction, button: discord.ui.Button):
        del button
        if not await self._owner(interaction):
            return
        updated = not self.digital_only
        result = await self.cog.run(
            self.cog.service.update_profile,
            self.guild_id,
            self.user_id,
            digital_only=updated,
        )
        if not result.ok:
            await interaction.response.send_message(result.message, ephemeral=True)
            return
        self.digital_only = updated
        if updated:
            text = "Digital gift is on. An address is not required."
        else:
            text = "Digital gift is off. An address is required before the draw."
        await interaction.response.send_message(text, ephemeral=True)

    async def on_error(self, interaction, error, item, /) -> None:
        del item
        await report_component_failure(
            f"ProfileView guild {self.guild_id} user {self.user_id}",
            interaction,
            error,
        )


class NotificationSettingsModal(discord.ui.Modal, title="Reminder settings"):
    def __init__(self, cog, guild_id: str, settings: dict):
        super().__init__()
        self.cog = cog
        self.guild_id = guild_id
        self.profile_reminders = _field(
            "Profile reminders on/off",
            "on" if settings.get("profile_reminders", True) else "off",
            limit=5,
            required=True,
        )
        self.shipping_reminders = _field(
            "Shipping reminders on/off",
            "on" if settings.get("shipping_reminders", True) else "off",
            limit=5,
            required=True,
        )
        self.interval_days = _field(
            "Days between reminders",
            str(settings.get("reminder_interval_days", 7)),
            limit=2,
            required=True,
        )
        self.lead_days = _field(
            "Days before deadline",
            str(settings.get("shipping_lead_days", 3)),
            limit=2,
            required=True,
        )
        for item in (self.profile_reminders, self.shipping_reminders, self.interval_days, self.lead_days):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            result = await self.cog.run(
                self.cog.service.update_notification_settings,
                self.guild_id,
                str(interaction.user.id),
                _interaction_is_admin(interaction),
                self.profile_reminders.value,
                self.shipping_reminders.value,
                self.interval_days.value,
                self.lead_days.value,
            )
            if result.ok and result.card is not None:
                await interaction.followup.send(
                    "Notification settings saved.",
                    embed=to_embed(result.card),
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(result.message, ephemeral=True)
        except Exception as exc:
            report_failure(f"notification settings guild {self.guild_id}", exc)
            await interaction.followup.send("The notification settings could not be saved.", ephemeral=True)

    async def on_error(self, interaction, error, /) -> None:
        await report_component_failure(f"notification settings guild {self.guild_id}", interaction, error)


class NotificationSettingsView(discord.ui.View):
    def __init__(self, cog, guild_id: str):
        super().__init__(timeout=180)
        self.cog = cog
        self.guild_id = guild_id

    @discord.ui.button(label="Edit notifications", style=discord.ButtonStyle.primary)
    async def edit(self, interaction: discord.Interaction, button: discord.ui.Button):
        del button
        result = await self.cog.run(
            self.cog.service.notification_settings,
            self.guild_id,
            str(interaction.user.id),
            _interaction_is_admin(interaction),
        )
        if not result.ok:
            await interaction.response.send_message(result.message, ephemeral=True)
            return
        await interaction.response.send_modal(
            NotificationSettingsModal(self.cog, self.guild_id, result.data or {})
        )

    async def on_error(self, interaction, error, item, /) -> None:
        del item
        await report_component_failure(f"notification settings guild {self.guild_id}", interaction, error)


class SetupModal(discord.ui.Modal, title="Event setup"):
    def __init__(self, cog, guild_id: str, user_id: str):
        super().__init__()
        self.cog = cog
        self.guild_id = str(guild_id)
        self.user_id = str(user_id)
        self.signup = discord.ui.TextInput(
            label="Last day to join",
            placeholder="2026-12-01, or clear",
            required=False,
            max_length=20,
        )
        self.deadline = discord.ui.TextInput(
            label="Gift deadline",
            placeholder="2026-12-20, or clear",
            required=False,
            max_length=20,
        )
        self.cap = discord.ui.TextInput(
            label="Spending cap",
            placeholder="$25, or clear",
            required=False,
            max_length=80,
        )
        self.rules = discord.ui.TextInput(
            label="Rules note",
            style=discord.TextStyle.paragraph,
            placeholder="Handmade gifts, or clear",
            required=False,
            max_length=500,
        )
        for item in (self.signup, self.deadline, self.cap, self.rules):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        if str(interaction.user.id) != self.user_id:
            await interaction.response.send_message("This form belongs to the person who opened it.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            result = await self.cog.run(
                self.cog.service.apply_setup,
                self.guild_id,
                str(interaction.user.id),
                _interaction_is_admin(interaction),
                self.signup.value,
                self.deadline.value,
                self.cap.value,
                self.rules.value,
            )
            if result.card is not None:
                await interaction.followup.send(result.message, embed=to_embed(result.card), ephemeral=True)
            else:
                await interaction.followup.send(result.message, ephemeral=True)
        except Exception as exc:
            report_failure(f"setup form guild {self.guild_id}", exc)
            await interaction.followup.send("The setup could not be saved.", ephemeral=True)

    async def on_error(self, interaction, error, /) -> None:
        await report_component_failure(f"setup form guild {self.guild_id}", interaction, error)


class AnnounceChannelSelect(discord.ui.ChannelSelect):
    def __init__(self):
        super().__init__(
            placeholder="Channel that should hear signup is open",
            channel_types=[discord.ChannelType.text, discord.ChannelType.news],
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        if not isinstance(view, SetupView):
            await interaction.response.send_message("This menu is not available right now.", ephemeral=True)
            return
        if str(interaction.user.id) != view.user_id:
            await interaction.response.send_message(
                "This guide belongs to the person who opened it.",
                ephemeral=True,
            )
            return
        channel = await _resolve_text_channel(interaction, self.values[0])
        if channel is None:
            await interaction.response.send_message("Choose a text channel in this server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            _ok, message = await view.cog.post_open_announcement(
                view.guild_id,
                str(interaction.user.id),
                _interaction_is_admin(interaction),
                channel,
            )
            await interaction.followup.send(message, ephemeral=True)
        except Exception as exc:
            report_failure(f"announce guild {view.guild_id}", exc)
            await interaction.followup.send("The announcement could not be posted.", ephemeral=True)


class SetupView(discord.ui.View):
    def __init__(self, cog, guild_id: str, user_id: str):
        super().__init__(timeout=180)
        self.cog = cog
        self.guild_id = str(guild_id)
        self.user_id = str(user_id)
        self.add_item(AnnounceChannelSelect())

    @discord.ui.button(label="Set dates, cap, and rules", style=discord.ButtonStyle.primary)
    async def edit(self, interaction: discord.Interaction, button: discord.ui.Button):
        del button
        if str(interaction.user.id) != self.user_id:
            await interaction.response.send_message(
                "This guide belongs to the person who opened it.",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(SetupModal(self.cog, self.guild_id, self.user_id))

    async def on_error(self, interaction, error, item, /) -> None:
        del item
        await report_component_failure(f"SetupView guild {self.guild_id}", interaction, error)


async def _resolve_text_channel(interaction, selected):
    channel_id = getattr(selected, "id", None)
    guild = getattr(interaction, "guild", None)
    if guild is not None and channel_id is not None:
        found = guild.get_channel(int(channel_id))
        if found is None and hasattr(guild, "fetch_channel"):
            try:
                found = await guild.fetch_channel(int(channel_id))
            except (discord.Forbidden, discord.HTTPException):
                found = None
        if found is not None:
            return found
    if hasattr(selected, "send"):
        return selected
    return None


class PastPartnerSelect(discord.ui.Select):
    """Finished-event partners. The person chooses which ones to skip again."""

    def __init__(self, options: list[discord.SelectOption]):
        super().__init__(
            placeholder="Choose who to skip again",
            min_values=1,
            max_values=len(options),
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        if not isinstance(view, PastPartnerView):
            await interaction.response.send_message("This menu is not available right now.", ephemeral=True)
            return
        if str(interaction.user.id) != view.user_id:
            await interaction.response.send_message("This menu belongs to the person who joined.", ephemeral=True)
            return
        names = {str(person["user_id"]): person.get("name") or "" for person in view.partners}
        chosen = [(value, names.get(value, "")) for value in self.values if value in names]
        if not chosen:
            await interaction.response.send_message("Choose someone from the list.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        result = await view.cog.run(
            view.cog.service.skip_past_matches,
            view.guild_id,
            view.user_id,
            chosen,
        )
        await interaction.followup.send(result.message, ephemeral=True)


class PastPartnerView(discord.ui.View):
    def __init__(self, cog, guild_id: str, user_id: str, partners: list[dict]):
        super().__init__(timeout=180)
        self.cog = cog
        self.guild_id = str(guild_id)
        self.user_id = str(user_id)
        self.partners = [person for person in partners if person.get("user_id")][:25]
        options = [
            discord.SelectOption(
                label=(person.get("name") or "Someone")[:100],
                value=str(person["user_id"])[:100],
            )
            for person in self.partners
        ]
        if options:
            self.add_item(PastPartnerSelect(options))


def _interaction_is_admin(interaction) -> bool:
    permissions = getattr(getattr(interaction, "user", None), "guild_permissions", None)
    return bool(permissions and permissions.administrator)


def _field(label: str, value, *, paragraph: bool = False, limit: int, required: bool = False) -> discord.ui.TextInput:
    style = discord.TextStyle.paragraph if paragraph else discord.TextStyle.short
    default = (value or "").strip() or None
    return discord.ui.TextInput(
        label=label,
        style=style,
        default=default,
        max_length=limit,
        required=required,
    )
