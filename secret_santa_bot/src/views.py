"""Discord forms for profiles and anonymous replies."""

from __future__ import annotations

import discord

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
        self.do_not_gift = _field("Do not gift", profile.get("do_not_gift"), limit=500)
        self.shirt_size = _field("Shirt size", profile.get("shirt_size"), limit=50)
        self.notes = _field("Notes", profile.get("notes"), paragraph=True, limit=500)
        self.address = _field("Address, blank if digital", profile.get("address"), paragraph=True, limit=1000)
        for item in (self.wishlist, self.do_not_gift, self.shirt_size, self.notes, self.address):
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
                do_not_gift=self.do_not_gift.value,
                shirt_size=self.shirt_size.value,
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


class ProfileView(discord.ui.View):
    def __init__(self, cog, guild_id: str, user_id: str, digital_only: bool):
        super().__init__(timeout=180)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        self.digital_only = digital_only

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


def _field(label: str, value, *, paragraph: bool = False, limit: int) -> discord.ui.TextInput:
    style = discord.TextStyle.paragraph if paragraph else discord.TextStyle.short
    default = (value or "").strip() or None
    return discord.ui.TextInput(
        label=label,
        style=style,
        default=default,
        max_length=limit,
        required=False,
    )
