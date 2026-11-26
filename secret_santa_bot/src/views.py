"""Discord forms for profiles and anonymous replies."""

from __future__ import annotations

import asyncio
import time
from datetime import date, datetime

import discord

from present import to_embed, tutorial_card, tutorial_page_count, tutorial_page_from_footer
from utils import log_event, report_failure


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
                await self.cog.notify_profile_change(result.data)
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


class DeliveryWindowModal(discord.ui.Modal, title="Delivery window"):
    def __init__(self, cog, guild_id: str, user_id: str, current: object):
        super().__init__()
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        saved = str(current or "").strip()
        self.window = discord.ui.TextInput(
            label="When a package can arrive",
            style=discord.TextStyle.paragraph,
            placeholder="Away next week and Dec 8-22. Don't leave it at the door.",
            default=saved or None,
            required=False,
            max_length=300,
        )
        self.add_item(self.window)

    async def on_submit(self, interaction: discord.Interaction):
        if str(interaction.user.id) != self.user_id:
            await interaction.response.send_message("This form belongs to someone else.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        text = self.window.value.strip()
        if text.lower() == "clear":
            text = ""
        try:
            result = await self.cog.run(
                self.cog.service.update_profile,
                self.guild_id,
                self.user_id,
                delivery_window=text,
            )
            if result.ok and result.data and result.data.get("notify_gifter_id"):
                await self.cog.notify_profile_change(result.data)
            await interaction.followup.send(result.message, ephemeral=True)
        except Exception as exc:
            report_failure(f"delivery window guild {self.guild_id} user {self.user_id}", exc)
            await interaction.followup.send("The delivery window could not be saved.", ephemeral=True)

    async def on_error(self, interaction, error, /) -> None:
        await report_component_failure(
            f"delivery window guild {self.guild_id} user {self.user_id}",
            interaction,
            error,
        )


def _component_cog(interaction, stored):
    if stored is not None:
        return stored
    return _secret_santa_cog(interaction)


_click_lock = asyncio.Lock()
_seen_clicks: dict[int, float] = {}


def _click_key(interaction) -> int:
    key = getattr(interaction, "id", None)
    if isinstance(key, int):
        return key
    response = getattr(interaction, "response", None)
    return id(response if response is not None else interaction)


async def _first_dispatch(interaction) -> bool:
    """One click can reach both the live view and the dynamic item.

    Discord runs both when the message view is still in memory. A second run
    of a toggle would undo the first. The same interaction id is kept for a
    few minutes so the later task does nothing.
    """
    key = _click_key(interaction)
    now = time.monotonic()
    async with _click_lock:
        stale = [item for item, seen in _seen_clicks.items() if now - seen > 900]
        for item in stale:
            del _seen_clicks[item]
        if key in _seen_clicks:
            return False
        _seen_clicks[key] = now
        return True


class PastMatchSelect(
    discord.ui.DynamicItem[discord.ui.UserSelect],
    template=r"ss:profile:past:(?P<guild_id>[^:]+):(?P<user_id>[^:]+)",
):
    """Server members, including people who have not joined this Secret Santa.

    The id is stored on the menu, so a click still works after the message
    view's timer would have stopped it or the process restarted.
    """

    def __init__(self, guild_id: str, user_id: str, cog=None):
        self.guild_id = str(guild_id)
        self.user_id = str(user_id)
        self.cog = cog
        super().__init__(
            discord.ui.UserSelect(
                placeholder="Someone you were paired with before",
                min_values=1,
                max_values=10,
                custom_id=f"ss:profile:past:{self.guild_id}:{self.user_id}",
            )
        )

    @property
    def placeholder(self) -> str:
        return self.item.placeholder

    @property
    def values(self):
        return self.item.values

    @property
    def _values(self):
        return self.item._values

    @_values.setter
    def _values(self, value) -> None:
        self.item._values = value

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        del interaction, item
        return cls(match["guild_id"], match["user_id"])

    async def callback(self, interaction: discord.Interaction):
        try:
            if not await _first_dispatch(interaction):
                return
            if interaction.response.is_done():
                return
            if str(interaction.user.id) != self.user_id:
                await interaction.response.send_message(
                    "This menu belongs to the person who opened the profile.",
                    ephemeral=True,
                )
                return
            cog = _component_cog(interaction, self.cog)
            if cog is None:
                await interaction.response.send_message("Secret Santa is not available right now.", ephemeral=True)
                return
            people = []
            for member in self.item.values:
                if getattr(member, "bot", False):
                    continue
                people.append((str(member.id), getattr(member, "display_name", "") or ""))
            if not people:
                await interaction.response.send_message("Pick a person. Bots are not in the draw.", ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True)
            result = await cog.run(
                cog.service.skip_past_matches,
                self.guild_id,
                self.user_id,
                people,
            )
            await interaction.followup.send(result.message, ephemeral=True)
        except discord.InteractionResponded:
            return
        except Exception as exc:
            await report_component_failure(
                f"past match menu guild {self.guild_id} user {self.user_id}",
                interaction,
                exc,
            )


def _profile_button(action: str, label: str, style: discord.ButtonStyle, guild_id: str, user_id: str):
    return discord.ui.Button(
        label=label,
        style=style,
        custom_id=f"ss:profile:{action}:{guild_id}:{user_id}",
    )


async def _profile_ready(button, interaction: discord.Interaction):
    if not await _first_dispatch(interaction):
        return None
    if interaction.response.is_done():
        return None
    if str(interaction.user.id) != button.user_id:
        await interaction.response.send_message(
            "These buttons belong to the person who opened them.",
            ephemeral=True,
        )
        return None
    cog = _component_cog(interaction, button.cog)
    if cog is None:
        await interaction.response.send_message("Secret Santa is not available right now.", ephemeral=True)
        return None
    return cog


class ProfileEditButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"ss:profile:edit:(?P<guild_id>[^:]+):(?P<user_id>[^:]+)",
):
    def __init__(self, guild_id: str, user_id: str, cog=None):
        self.guild_id = str(guild_id)
        self.user_id = str(user_id)
        self.cog = cog
        super().__init__(
            _profile_button("edit", "Edit profile", discord.ButtonStyle.primary, self.guild_id, self.user_id)
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        del interaction, item
        return cls(match["guild_id"], match["user_id"])

    async def callback(self, interaction: discord.Interaction):
        try:
            cog = await _profile_ready(self, interaction)
            if cog is None:
                return
            info = await cog.run(cog.service.my_info, self.guild_id, self.user_id)
            if not info.ok:
                await interaction.response.send_message(info.message, ephemeral=True)
                return
            await interaction.response.send_modal(ProfileModal(cog, self.guild_id, self.user_id, info.data or {}))
        except discord.InteractionResponded:
            return
        except Exception as exc:
            await report_component_failure(f"profile edit guild {self.guild_id} user {self.user_id}", interaction, exc)


class ProfileWindowButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"ss:profile:window:(?P<guild_id>[^:]+):(?P<user_id>[^:]+)",
):
    def __init__(self, guild_id: str, user_id: str, cog=None):
        self.guild_id = str(guild_id)
        self.user_id = str(user_id)
        self.cog = cog
        super().__init__(
            _profile_button("window", "Delivery window", discord.ButtonStyle.secondary, self.guild_id, self.user_id)
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        del interaction, item
        return cls(match["guild_id"], match["user_id"])

    async def callback(self, interaction: discord.Interaction):
        try:
            cog = await _profile_ready(self, interaction)
            if cog is None:
                return
            info = await cog.run(cog.service.my_info, self.guild_id, self.user_id)
            if not info.ok:
                await interaction.response.send_message(info.message, ephemeral=True)
                return
            await interaction.response.send_modal(
                DeliveryWindowModal(
                    cog,
                    self.guild_id,
                    self.user_id,
                    (info.data or {}).get("delivery_window"),
                )
            )
        except discord.InteractionResponded:
            return
        except Exception as exc:
            await report_component_failure(
                f"delivery window button guild {self.guild_id} user {self.user_id}",
                interaction,
                exc,
            )


class ProfileDigitalButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"ss:profile:digital:(?P<guild_id>[^:]+):(?P<user_id>[^:]+)",
):
    def __init__(self, guild_id: str, user_id: str, cog=None):
        self.guild_id = str(guild_id)
        self.user_id = str(user_id)
        self.cog = cog
        super().__init__(
            _profile_button(
                "digital",
                "Toggle digital gift",
                discord.ButtonStyle.secondary,
                self.guild_id,
                self.user_id,
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        del interaction, item
        return cls(match["guild_id"], match["user_id"])

    async def callback(self, interaction: discord.Interaction):
        try:
            cog = await _profile_ready(self, interaction)
            if cog is None:
                return
            info = await cog.run(cog.service.my_info, self.guild_id, self.user_id)
            if not info.ok:
                await interaction.response.send_message(info.message, ephemeral=True)
                return
            updated = not bool((info.data or {}).get("digital_only"))
            result = await cog.run(
                cog.service.update_profile,
                self.guild_id,
                self.user_id,
                digital_only=updated,
            )
            if not result.ok:
                await interaction.response.send_message(result.message, ephemeral=True)
                return
            if updated:
                text = "Digital gift is on. An address is not required."
            else:
                text = "Digital gift is off. An address is required before the draw."
            await interaction.response.send_message(text, ephemeral=True)
        except discord.InteractionResponded:
            return
        except Exception as exc:
            await report_component_failure(
                f"digital toggle guild {self.guild_id} user {self.user_id}",
                interaction,
                exc,
            )


class ProfileView(discord.ui.View):
    def __init__(self, cog, guild_id: str, user_id: str, digital_only: bool, in_guild: bool = False):
        # No timer. The buttons are dynamic items, so a click still has a
        # listener after this process restarts.
        super().__init__(timeout=None)
        self.cog = cog
        self.guild_id = str(guild_id)
        self.user_id = str(user_id)
        self.digital_only = digital_only
        if in_guild:
            self.add_item(PastMatchSelect(self.guild_id, self.user_id, cog))
        self.edit = ProfileEditButton(self.guild_id, self.user_id, cog)
        self.delivery_window = ProfileWindowButton(self.guild_id, self.user_id, cog)
        self.toggle_digital = ProfileDigitalButton(self.guild_id, self.user_id, cog)
        self.add_item(self.edit)
        self.add_item(self.delivery_window)
        self.add_item(self.toggle_digital)

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


class SettingsEditButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"ss:settings:(?P<guild_id>[^:]+)",
):
    def __init__(self, guild_id: str, cog=None):
        self.guild_id = str(guild_id)
        self.cog = cog
        super().__init__(
            discord.ui.Button(
                label="Edit notifications",
                style=discord.ButtonStyle.primary,
                custom_id=f"ss:settings:{self.guild_id}",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        del interaction, item
        return cls(match["guild_id"])

    async def callback(self, interaction: discord.Interaction):
        try:
            if not await _first_dispatch(interaction):
                return
            if interaction.response.is_done():
                return
            cog = _component_cog(interaction, self.cog)
            if cog is None:
                await interaction.response.send_message("Secret Santa is not available right now.", ephemeral=True)
                return
            result = await cog.run(
                cog.service.notification_settings,
                self.guild_id,
                str(interaction.user.id),
                _interaction_is_admin(interaction),
            )
            if not result.ok:
                await interaction.response.send_message(result.message, ephemeral=True)
                return
            await interaction.response.send_modal(
                NotificationSettingsModal(cog, self.guild_id, result.data or {})
            )
        except discord.InteractionResponded:
            return
        except Exception as exc:
            await report_component_failure(f"notification settings guild {self.guild_id}", interaction, exc)


class NotificationSettingsView(discord.ui.View):
    def __init__(self, cog, guild_id: str):
        super().__init__(timeout=None)
        self.cog = cog
        self.guild_id = str(guild_id)
        self.edit = SettingsEditButton(self.guild_id, cog)
        self.add_item(self.edit)

    async def on_error(self, interaction, error, item, /) -> None:
        del item
        await report_component_failure(f"notification settings guild {self.guild_id}", interaction, error)


def _form_default(value, *, day: bool = False) -> str | None:
    if value is None or value == "":
        return None
    if day:
        if isinstance(value, datetime):
            return value.date().isoformat()
        if isinstance(value, date):
            return value.isoformat()
        text = str(value).strip()
        return text or None
    text = " ".join(str(value).split())
    return text or None


class SetupModal(discord.ui.Modal, title="Event setup"):
    def __init__(self, cog, guild_id: str, user_id: str, event: dict | None = None):
        super().__init__()
        self.cog = cog
        self.guild_id = str(guild_id)
        self.user_id = str(user_id)
        current = event or {}
        self.signup = discord.ui.TextInput(
            label="Last day to join",
            placeholder="2026-12-01, or clear",
            default=_form_default(current.get("signup_deadline"), day=True),
            required=False,
            max_length=20,
        )
        self.deadline = discord.ui.TextInput(
            label="Gift deadline",
            placeholder="2026-12-20, or clear",
            default=_form_default(current.get("deadline"), day=True),
            required=False,
            max_length=20,
        )
        self.cap = discord.ui.TextInput(
            label="Spending cap",
            placeholder="$25, or clear",
            default=_form_default(current.get("spending_cap")),
            required=False,
            max_length=80,
        )
        self.rules = discord.ui.TextInput(
            label="Rules note",
            style=discord.TextStyle.paragraph,
            placeholder="Handmade gifts, or clear",
            default=_form_default(current.get("rules")),
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


def _secret_santa_cog(interaction):
    client = getattr(interaction, "client", None)
    getter = getattr(client, "get_cog", None)
    if getter is None:
        return None
    return getter("SecretSantaCog")


class SetupButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"ss:setup:(?P<guild_id>[0-9]+):(?P<user_id>[0-9]+)",
):
    """Opens the setup form from the button id, including after a restart.

    A normal view is dropped when the process restarts or its timer ends.
    Discord then shows "didn't respond in time" because nothing acknowledges
    the click. The guild and the opener are in the custom id, so this button
    can answer without that view.
    """

    def __init__(self, guild_id: str, user_id: str):
        self.guild_id = str(guild_id)
        self.user_id = str(user_id)
        super().__init__(
            discord.ui.Button(
                label="Set dates, cap, and rules",
                style=discord.ButtonStyle.primary,
                custom_id=f"ss:setup:{self.guild_id}:{self.user_id}",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        del interaction, item
        return cls(match["guild_id"], match["user_id"])

    async def callback(self, interaction: discord.Interaction):
        try:
            if not await _first_dispatch(interaction):
                return
            if interaction.response.is_done():
                return
            if str(interaction.user.id) != self.user_id:
                await interaction.response.send_message(
                    "This guide belongs to the person who opened it.",
                    ephemeral=True,
                )
                return
            cog = _secret_santa_cog(interaction)
            if cog is None:
                await interaction.response.send_message(
                    "Secret Santa is not available right now.",
                    ephemeral=True,
                )
                return
            overview = await cog.run(
                cog.service.setup_overview,
                self.guild_id,
                str(interaction.user.id),
                _interaction_is_admin(interaction),
            )
            if not overview.ok:
                await interaction.response.send_message(overview.message, ephemeral=True)
                return
            event = (overview.data or {}).get("event") or {}
            await interaction.response.send_modal(SetupModal(cog, self.guild_id, self.user_id, event))
        except discord.InteractionResponded:
            return
        except Exception as exc:
            await report_component_failure(f"setup button guild {self.guild_id}", interaction, exc)


class SetupBlockButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"ss:blockbtn:(?P<guild_id>[0-9]+):(?P<user_id>[0-9]+)",
):
    """Opens a private pair picker. The server channel never sees the names."""

    def __init__(self, guild_id: str, user_id: str):
        self.guild_id = str(guild_id)
        self.user_id = str(user_id)
        super().__init__(
            discord.ui.Button(
                label="Block a match",
                style=discord.ButtonStyle.secondary,
                custom_id=f"ss:blockbtn:{self.guild_id}:{self.user_id}",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        del interaction, item
        return cls(match["guild_id"], match["user_id"])

    async def callback(self, interaction: discord.Interaction):
        try:
            if not await _first_dispatch(interaction):
                return
            if interaction.response.is_done():
                return
            if str(interaction.user.id) != self.user_id:
                await interaction.response.send_message(
                    "This guide belongs to the person who opened it.",
                    ephemeral=True,
                )
                return
            if _secret_santa_cog(interaction) is None:
                await interaction.response.send_message(
                    "Secret Santa is not available right now.",
                    ephemeral=True,
                )
                return
            await interaction.response.send_message(
                "Choose two people who should not be matched. Their names stay in your DMs.",
                view=SetupBlockPickView(self.guild_id, self.user_id),
                ephemeral=True,
            )
        except discord.InteractionResponded:
            return
        except Exception as exc:
            await report_component_failure(f"block button guild {self.guild_id}", interaction, exc)


class SetupBlockSelect(
    discord.ui.DynamicItem[discord.ui.UserSelect],
    template=r"ss:blockpick:(?P<guild_id>[0-9]+):(?P<user_id>[0-9]+)",
):
    """Locks a pair from the private picker, including after a restart."""

    def __init__(self, guild_id: str, user_id: str):
        self.guild_id = str(guild_id)
        self.user_id = str(user_id)
        super().__init__(
            discord.ui.UserSelect(
                placeholder="Two people who should not be matched",
                min_values=2,
                max_values=2,
                custom_id=f"ss:blockpick:{self.guild_id}:{self.user_id}",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        del interaction, item
        return cls(match["guild_id"], match["user_id"])

    async def callback(self, interaction: discord.Interaction):
        try:
            if not await _first_dispatch(interaction):
                return
            if interaction.response.is_done():
                return
            if str(interaction.user.id) != self.user_id:
                await interaction.response.send_message(
                    "This guide belongs to the person who opened it.",
                    ephemeral=True,
                )
                return
            cog = _secret_santa_cog(interaction)
            if cog is None:
                await interaction.response.send_message(
                    "Secret Santa is not available right now.",
                    ephemeral=True,
                )
                return
            chosen = list(self.item.values or [])
            if len(chosen) != 2 or any(getattr(person, "bot", False) for person in chosen):
                await interaction.response.send_message(
                    "Pick two people. Bots are not in the draw.",
                    ephemeral=True,
                )
                return
            first, second = chosen
            await interaction.response.defer(ephemeral=True)
            result = await cog.run(
                cog.service.block_pair,
                self.guild_id,
                str(interaction.user.id),
                _interaction_is_admin(interaction),
                str(first.id),
                getattr(first, "display_name", None) or "Someone",
                str(second.id),
                getattr(second, "display_name", None) or "Someone",
            )
            for note in (result.data or {}).get("changed") or []:
                sender = getattr(cog, "_dm_assignment", None)
                if sender is not None:
                    await sender(note)
            if result.ok:
                log_event("BLOCK", f"guild {self.guild_id} user {interaction.user.id}")
            try:
                await interaction.user.send(result.message)
            except discord.Forbidden:
                await interaction.followup.send(
                    "I couldn't DM you. Allow DMs from server members.",
                    ephemeral=True,
                )
                return
            await interaction.followup.send("**Sent in a DM.**", ephemeral=True)
        except discord.InteractionResponded:
            return
        except Exception as exc:
            await report_component_failure(f"block picker guild {self.guild_id}", interaction, exc)


class SetupBlockPickView(discord.ui.View):
    def __init__(self, guild_id: str, user_id: str):
        super().__init__(timeout=None)
        self.add_item(SetupBlockSelect(guild_id, user_id))


class AnnounceChannelPicker(
    discord.ui.DynamicItem[discord.ui.ChannelSelect],
    template=r"ss:announce:(?P<guild_id>[0-9]+):(?P<user_id>[0-9]+)",
):
    """Posts the open announcement from the menu id, including after a restart."""

    def __init__(self, guild_id: str, user_id: str):
        self.guild_id = str(guild_id)
        self.user_id = str(user_id)
        super().__init__(
            discord.ui.ChannelSelect(
                placeholder="Channel that should hear signup is open",
                channel_types=[discord.ChannelType.text, discord.ChannelType.news],
                min_values=1,
                max_values=1,
                custom_id=f"ss:announce:{self.guild_id}:{self.user_id}",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        del interaction, item
        return cls(match["guild_id"], match["user_id"])

    async def callback(self, interaction: discord.Interaction):
        try:
            if not await _first_dispatch(interaction):
                return
            if interaction.response.is_done():
                return
            if str(interaction.user.id) != self.user_id:
                await interaction.response.send_message(
                    "This guide belongs to the person who opened it.",
                    ephemeral=True,
                )
                return
            cog = _secret_santa_cog(interaction)
            if cog is None:
                await interaction.response.send_message(
                    "Secret Santa is not available right now.",
                    ephemeral=True,
                )
                return
            selected = self.item.values[0] if self.item.values else None
            await interaction.response.defer(ephemeral=True)
            channel = await _resolve_text_channel(interaction, selected)
            if channel is None:
                await interaction.followup.send("Choose a text channel in this server.", ephemeral=True)
                return
            _ok, message = await cog.post_open_announcement(
                self.guild_id,
                str(interaction.user.id),
                _interaction_is_admin(interaction),
                channel,
            )
            await interaction.followup.send(message, ephemeral=True)
        except discord.InteractionResponded:
            return
        except Exception as exc:
            await report_component_failure(f"announce guild {self.guild_id}", interaction, exc)


class SetupView(discord.ui.View):
    def __init__(self, guild_id: str, user_id: str):
        # No timer. The buttons are dynamic items, so a click still has a
        # listener after this process restarts.
        super().__init__(timeout=None)
        self.guild_id = str(guild_id)
        self.user_id = str(user_id)
        self.add_item(AnnounceChannelPicker(self.guild_id, self.user_id))
        self.add_item(SetupButton(self.guild_id, self.user_id))
        self.add_item(SetupBlockButton(self.guild_id, self.user_id))


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


class PastPartnerSelect(
    discord.ui.DynamicItem[discord.ui.Select],
    template=r"ss:past:(?P<guild_id>[^:]+):(?P<user_id>[^:]+)",
):
    """Finished-event partners. The person chooses which ones to skip again."""

    def __init__(self, guild_id: str, user_id: str, options: list[discord.SelectOption], cog=None):
        self.guild_id = str(guild_id)
        self.user_id = str(user_id)
        self.cog = cog
        super().__init__(
            discord.ui.Select(
                placeholder="Choose who to skip again",
                min_values=1,
                max_values=len(options),
                options=options,
                custom_id=f"ss:past:{self.guild_id}:{self.user_id}",
            )
        )

    @property
    def _values(self):
        return self.item._values

    @_values.setter
    def _values(self, value) -> None:
        self.item._values = value

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        del interaction, item
        option = discord.SelectOption(label="Saved", value="saved")
        return cls(match["guild_id"], match["user_id"], [option])

    async def callback(self, interaction: discord.Interaction):
        try:
            if not await _first_dispatch(interaction):
                return
            if interaction.response.is_done():
                return
            if str(interaction.user.id) != self.user_id:
                await interaction.response.send_message(
                    "This menu belongs to the person who joined.",
                    ephemeral=True,
                )
                return
            cog = _component_cog(interaction, self.cog)
            if cog is None:
                await interaction.response.send_message("Secret Santa is not available right now.", ephemeral=True)
                return
            names = {}
            view = self.view
            if isinstance(view, PastPartnerView):
                names = {str(person["user_id"]): person.get("name") or "" for person in view.partners}
            else:
                rows = await cog.run(cog.service.db.list_past_partners, self.guild_id, self.user_id)
                names = {str(person["user_id"]): person.get("name") or "" for person in rows}
            chosen = [(value, names.get(value, "")) for value in self.item.values if value in names]
            if not chosen:
                await interaction.response.send_message("Choose someone from the list.", ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True)
            result = await cog.run(
                cog.service.skip_past_matches,
                self.guild_id,
                self.user_id,
                chosen,
            )
            await interaction.followup.send(result.message, ephemeral=True)
        except discord.InteractionResponded:
            return
        except Exception as exc:
            await report_component_failure(
                f"past partner menu guild {self.guild_id} user {self.user_id}",
                interaction,
                exc,
            )


class PastPartnerView(discord.ui.View):
    def __init__(self, cog, guild_id: str, user_id: str, partners: list[dict]):
        super().__init__(timeout=None)
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
            self.add_item(PastPartnerSelect(self.guild_id, self.user_id, options, cog))


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
