"""Discord cards for the messages people actually read."""

from __future__ import annotations

from dataclasses import dataclass

import discord

SANTA_RED = 0xC23B22
GIFT_STATUSES = ("not_bought", "bought", "shipped", "received")
PHASE_DETAIL = {
    "signup": "People can still join, leave, and edit profiles.",
    "locked": "Signup is closed. Profiles can still be edited.",
    "drawn": "Matches are out. Gift tracking is open.",
    "done": "This event is archived.",
}
HELP_GROUPS = (
    (
        "Getting started",
        (
            "help",
            "info",
            "join",
            "leave",
            "profile",
            "setwishlist",
            "setaddress",
            "myinfo",
            "exclude",
            "unexclude",
        ),
    ),
    (
        "After the draw",
        ("match", "partnerinfo", "message", "gift"),
    ),
    ("Event status", ("status", "participants")),
    (
        "Organizer",
        (
            "create",
            "lock",
            "unlock",
            "deadline",
            "start",
            "finish",
            "cancel",
            "remove",
            "settings",
            "remind",
            "broadcast",
            "reveal",
            "logs",
            "ratelimit",
        ),
    ),
)


@dataclass(frozen=True)
class Field:
    name: str
    value: str
    inline: bool = False


@dataclass(frozen=True)
class Card:
    title: str
    description: str = ""
    fields: tuple[Field, ...] = ()
    footer: str = ""
    color: int = SANTA_RED

    def text(self) -> str:
        lines = [self.title]
        if self.description:
            lines.append(self.description)
        lines.extend(f"{item.name}: {item.value}" for item in self.fields)
        if self.footer:
            lines.append(self.footer)
        return "\n".join(lines)


def safe(value: object) -> str:
    """Keep user text from pinging or restyling the surrounding card."""
    text = str(value if value is not None else "")
    text = text.replace("\\", "\\\\")
    for char in ("*", "_", "`", "~", "|"):
        text = text.replace(char, f"\\{char}")
    return text.replace("@", "@\u200b")


def label(value: object, fallback: str) -> str:
    text = " ".join(safe(value).split())
    return text or fallback


def shown(value: object, fallback: str) -> str:
    text = safe(value).strip()
    return text or fallback


def guide_fields() -> tuple[Field, ...]:
    return (
        Field(
            "Your profile",
            "A wishlist is required. Add a shipping address too, unless the gift can be digital.\n"
            "Use `/profile` for the private form. `s!setwishlist` and `s!setaddress` also work, "
            "and those messages are deleted in a server channel.",
        ),
        Field(
            "The draw",
            "When every profile is complete, the organizer runs `s!start`. "
            "You are assigned one person, and someone else is assigned you. "
            "Nobody is assigned to themselves, and you are not told who is buying for you.\n"
            "The match arrives by DM. `s!match` sends it again.",
        ),
        Field(
            "Anonymous notes",
            "Write to your person with `s!message giftee`. They can write back with `s!message gifter`. "
            "Each note has a Reply button. Keep their name out of the server channel.",
        ),
        Field(
            "The gift",
            "Mark progress one step at a time: `s!gift bought`, then `s!gift shipped`. "
            "Your giftee marks `s!gift received` after it arrives. "
            "A reminder can arrive near the deadline when the organizer leaves shipping reminders on.",
        ),
        Field(
            "Privacy",
            "Wishlists and addresses are sent in DMs, not in the channel. "
            "`s!status` shows who is missing a profile, not what they wrote. "
            "The organizer does not see the pairs unless they confirm with `s!reveal reveal`.",
        ),
        Field(
            "If plans change",
            "On `/profile` in the server, the menu lets you skip someone you were paired with before, "
            "even if they have not joined this year. `s!exclude @someone` does the same. "
            "You will not be assigned to each other.\n"
            "`s!leave` drops you from the event. After the draw, leaving redraws the open seats "
            "and keeps every match it still can. If a redraw is impossible, nobody is removed.",
        ),
        Field(
            "Running the event",
            "`s!create` opens one event in this server. The organizer is not in the draw until they join.\n"
            "`s!lock`, `s!deadline`, and `s!settings` are optional. `s!settings` chooses which reminders "
            "go out and how many days apart. `s!remind` sends a profile reminder immediately.\n"
            "`s!finish` archives a drawn event so another can be created.",
        ),
    )


def welcome_card(person_name: object, event_name: object) -> Card:
    person = label(person_name, "there")
    event = label(event_name, "Secret Santa")
    return Card(
        title=f"Welcome to {event}"[:256],
        description=f"{person}, you're in. This is the whole event, from your profile to the gift.",
        fields=guide_fields(),
        footer="s!help lists every command. s!info shows this guide again.",
    )


def info_card() -> Card:
    return Card(
        title="How Secret Santa works",
        description=(
            "One open event per server. The organizer runs it and is not in the draw until they join."
        ),
        fields=guide_fields(),
        footer="Join with s!join. The same guide arrives in a DM.",
    )


def help_card(entries: list[tuple[str, str]], prefix: str = "s!") -> Card:
    known = {name: (description or "No description.") for name, description in entries}
    used: set[str] = set()
    fields: list[Field] = []
    for title, names in HELP_GROUPS:
        lines = []
        for name in names:
            if name not in known:
                continue
            used.add(name)
            lines.append(f"`{prefix}{name}` — {known[name]}")
        fields.extend(_field_chunks(title, lines))
    other = [
        f"`{prefix}{name}` — {description or 'No description.'}"
        for name, description in entries
        if name not in used
    ]
    fields.extend(_field_chunks("Other", other))
    if not fields:
        fields.append(Field("Commands", "No commands are available."))
    return Card(
        title="Secret Santa help",
        description="Commands are grouped by what you are doing. Slash commands do the same work.",
        fields=tuple(fields),
        footer="s!help join shows one command. s!info explains the event.",
    )


def format_help(entries: list[tuple[str, str]], prefix: str = "s!") -> str:
    return help_card(entries, prefix).text()


def command_help_card(signature: str, description: str, footer: str) -> Card:
    return Card(
        title=signature[:256] or "Command",
        description=description.strip() or "No description.",
        footer=footer,
    )


def status_card(payload: dict) -> Card:
    phase = str(payload.get("phase") or "signup")
    name = label(payload.get("name"), "Secret Santa")
    detail = PHASE_DETAIL.get(phase, "")
    fields = [
        Field("Phase", f"{phase}\n{detail}".strip(), inline=True),
        Field("Deadline", str(payload.get("deadline") or "Not set"), inline=True),
        Field("Participants", str(payload.get("count", 0)), inline=True),
    ]
    missing_wishlist = payload.get("missing_wishlist") or []
    missing_address = payload.get("missing_address") or []
    if missing_wishlist:
        fields.append(Field("Missing a wishlist", _name_list(missing_wishlist)))
    if missing_address:
        fields.append(Field("Missing an address", _name_list(missing_address)))
    counts = payload.get("gift_counts")
    if counts is not None:
        fields.append(
            Field(
                "Gifts",
                ", ".join(f"{counts[status]} {status.replace('_', ' ')}" for status in GIFT_STATUSES),
            )
        )
    description = f"{name} is in the {phase} phase."
    if detail:
        description = f"{description} {detail}"
    return Card(title=name[:256], description=description, fields=tuple(fields))


def roster_card(event_name: object, names: list[object]) -> Card:
    event = label(event_name, "Secret Santa")
    if not names:
        description = "Nobody has joined yet."
    else:
        lines = [f"{index}. {label(name, 'Someone')}" for index, name in enumerate(names, start=1)]
        description = "\n".join(lines)
    return Card(title=f"Participants in {event}"[:256], description=description)


def profile_card(participant: dict) -> Card:
    digital = "Yes" if participant.get("digital_only") else "No"
    return Card(
        title="Your profile",
        description="This stays in a DM. After the draw, your giver sees the wishlist.",
        fields=(
            Field("Wishlist", shown(participant.get("wishlist"), "Not set")),
            Field("Do not gift", shown(participant.get("do_not_gift"), "Not set")),
            Field("Notes", shown(participant.get("notes"), "Not set")),
            Field("Address", shown(participant.get("address"), "Not set")),
            Field("Digital gift", digital, inline=True),
        ),
        footer="Change it with /profile. A wishlist edit after the draw is sent to your giver.",
    )


def profile_prompt_card(in_guild: bool = True) -> Card:
    if in_guild:
        past_match = (
            "Use the menu to choose people from this server you were paired with before. "
            "They do not have to have joined yet. You will not be assigned to each other."
        )
    else:
        past_match = (
            "To skip someone from a past Secret Santa, run /profile in the server. "
            "The menu there lists people in the server, including people who have not joined yet."
        )
    return Card(
        title="Your profile",
        description="Use the buttons below. The form is private, so the address is not posted in the channel.",
        fields=(
            Field(
                "What the draw needs",
                "A wishlist is required. An address is required unless the gift is digital.",
            ),
            Field("Past matches", past_match),
        ),
        footer="s!myinfo sends the saved profile in a DM.",
    )


def match_card(receiver: dict) -> Card:
    if receiver.get("digital_only"):
        delivery = "Digital gift. No shipping address."
    else:
        delivery = shown(receiver.get("address"), "No address set")
    name = label(receiver.get("name"), "your giftee")
    return Card(
        title="Your match",
        description=f"You are getting a gift for **{name}**.",
        fields=(
            Field("Wishlist", shown(receiver.get("wishlist"), "No wishlist set")),
            Field("Do not gift", shown(receiver.get("do_not_gift"), "Not set")),
            Field("Notes", shown(receiver.get("notes"), "Not set")),
            Field("Delivery", delivery),
        ),
        footer="Message them with s!message giftee. Keep their name out of the server.",
    )


def settings_card(profile_on: bool, shipping_on: bool, interval: int, lead: int) -> Card:
    profile = "on" if profile_on else "off"
    shipping = "on" if shipping_on else "off"
    return Card(
        title="Reminder settings",
        description=(
            f"The bot checks once a day and sends a reminder after {interval} day(s).\n"
            f"Shipping reminders start {lead} day(s) before the deadline."
        ),
        fields=(
            Field("Profile reminders", f"{profile}."),
            Field("Shipping reminders", f"{shipping}."),
        ),
        footer="s!remind still sends a profile reminder immediately.",
    )


def profile_reminder_card(missing: list[str]) -> Card:
    needed = ", ".join(label(item, "profile") for item in missing) or "profile"
    return Card(
        title="Your profile is still incomplete",
        description=f"Secret Santa still needs your {needed}.",
        fields=(
            Field(
                "How to finish",
                "Use `/profile`, `s!setwishlist`, or `s!setaddress`. A digital gift does not need an address.",
            ),
        ),
    )


def draw_waiting_card(missing: list[str]) -> Card:
    needed = ", ".join(label(item, "profile") for item in missing) or "profile"
    return Card(
        title="The draw is waiting on you",
        description=f"The Secret Santa draw is waiting on your {needed}.",
        footer="Use /profile, s!setwishlist, or s!setaddress.",
    )


def shipping_reminder_card(deadline: object) -> Card:
    day = deadline.isoformat() if hasattr(deadline, "isoformat") else str(deadline)
    return Card(
        title="Your gift is not marked shipped",
        description=f"The deadline is {day}.",
        footer="When it goes out, run s!gift shipped.",
    )


def anonymous_card(direction: str, body: str, reply_as: str) -> Card:
    if direction == "giftee":
        title = "A note from your Secret Santa"
    else:
        title = "A note from your giftee"
    return Card(
        title=title,
        description=shown(body, "They sent a note with no text."),
        footer=f"Reply with the button, or s!message {reply_as}.",
    )


def announcement_card(text: str) -> Card:
    return Card(
        title="Announcement",
        description=shown(text, "The organizer sent an empty announcement."),
        footer="From the Secret Santa organizer.",
    )


def wishlist_update_card(wishlist: str) -> Card:
    return Card(
        title="Your giftee updated their wishlist",
        description=shown(wishlist, "Their wishlist is now empty."),
    )


def removed_card() -> Card:
    return Card(
        title="You were removed",
        description="The organizer removed you from the Secret Santa.",
    )


def reveal_cards(rows: list[dict]) -> list[Card]:
    lines = [
        f"{label(row.get('giver_name'), 'Someone')} -> {label(row.get('receiver_name'), 'Someone')}"
        for row in rows
    ]
    footer = "Anonymity is broken for you. This list was not posted in the server."
    if not lines:
        return [Card(title="Full draw", description="No pairs were stored.", footer=footer)]
    pages: list[list[str]] = []
    current: list[str] = []
    size = 0
    for line in lines:
        if current and size + len(line) + 1 > 3500:
            pages.append(current)
            current = []
            size = 0
        current.append(line)
        size += len(line) + 1
    if current:
        pages.append(current)
    cards = []
    for index, page in enumerate(pages, start=1):
        title = "Full draw" if len(pages) == 1 else f"Full draw ({index}/{len(pages)})"
        cards.append(Card(title=title, description="\n".join(page), footer=footer))
    return cards


def reveal_card(rows: list[dict]) -> Card:
    return reveal_cards(rows)[0]


def to_embed(card: Card) -> discord.Embed:
    embed = discord.Embed(title=_clip(card.title, 256) or "Secret Santa", color=card.color)
    if card.description:
        embed.description = _clip(card.description, 4096)
    for item in card.fields[:25]:
        value = _clip(item.value, 1024)
        if not value:
            continue
        embed.add_field(name=_clip(item.name, 256) or "Details", value=value, inline=item.inline)
    if card.footer:
        embed.set_footer(text=_clip(card.footer, 2048))
    return embed


def _field_chunks(title: str, lines: list[str]) -> list[Field]:
    fields: list[Field] = []
    current: list[str] = []
    current_len = 0
    part = 1
    for line in lines:
        extra = len(line) + (1 if current else 0)
        if current and current_len + extra > 1000:
            name = title if part == 1 else f"{title} ({part})"
            fields.append(Field(name, "\n".join(current)))
            part += 1
            current = [line]
            current_len = len(line)
        else:
            current.append(line)
            current_len += extra
    if current:
        name = title if part == 1 else f"{title} ({part})"
        fields.append(Field(name, "\n".join(current)))
    return fields


def _name_list(names: list[object]) -> str:
    shown_names = [label(name, "Someone") for name in names]
    text = ", ".join(shown_names)
    if len(text) <= 1000:
        return text
    kept: list[str] = []
    size = 0
    for name in shown_names:
        extra = len(name) + (2 if kept else 0)
        if size + extra > 900:
            break
        kept.append(name)
        size += extra
    remaining = len(shown_names) - len(kept)
    return ", ".join(kept) + f", and {remaining} more"


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= 1:
        return text[:limit]
    return text[: limit - 1].rstrip() + "…"
