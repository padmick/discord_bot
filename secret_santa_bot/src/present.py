"""Discord cards for the messages people actually read."""

from __future__ import annotations

import re
from dataclasses import dataclass

import discord

# Bump this, and rewrite changelog_card, when a release should show a new note.
CHANGELOG_VERSION = 4

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
            "tutorial",
            "info",
            "join",
            "leave",
            "profile",
            "setwishlist",
            "setaddress",
            "setbudget",
            "myinfo",
            "exclude",
            "unexclude",
            "problem",
            "idea",
            "news",
        ),
    ),
    (
        "After the draw",
        ("match", "partnerinfo", "message", "notes", "gift"),
    ),
    ("Event status", ("status", "participants")),
    (
        "Organizer",
        (
            "create",
            "lock",
            "unlock",
            "deadline",
            "signupby",
            "start",
            "finish",
            "cancel",
            "remove",
            "settings",
            "remind",
            "broadcast",
            "reveal",
            "block",
            "unblock",
            "blocks",
            "carrylocks",
            "waiting",
            "cap",
            "rules",
            "logs",
            "reports",
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
    image: str = ""

    def text(self) -> str:
        lines = [self.title]
        if self.description:
            lines.append(self.description)
        lines.extend(f"{item.name}: {item.value}" for item in self.fields)
        if self.image:
            lines.append(self.image)
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


def spending_text(personal: object, cap: object = "") -> str:
    own = str(personal or "").strip()
    shared = str(cap or "").strip()
    if own:
        return shown(own, "Not set")
    if shared:
        return f"{shown(shared, 'Not set')} for this event"
    return "Not set"


def guide_fields() -> tuple[Field, ...]:
    return (
        Field(
            "Your profile",
            "A wishlist is required. Add a shipping address too, unless the gift can be digital. "
            "A spending limit is optional.\n"
            "Use `/profile` for the private form. `s!setwishlist`, `s!setaddress`, and `s!setbudget` also work, "
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
            "Emoji, GIF links, and attached pictures show in the note. Each note has a Reply button. "
            "`s!notes` DMs the latest notes again. Keep their name out of the server channel.",
        ),
        Field(
            "The gift",
            "Mark progress one step at a time: `s!gift bought`, then `s!gift shipped`. "
            "Add a tracking link with `s!gift shipped https://...`. Your giftee gets that link by DM "
            "and marks `s!gift received` after it arrives. "
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
            "`s!lock`, `s!deadline`, and `s!settings` are optional. `s!signupby` is the last day to join. "
            "`s!settings` chooses which reminders go out and how many days apart.\n"
            "`s!block` keeps two people apart. `s!carrylocks` copies last year's locks. "
            "`s!cap` sets a spending cap. `s!rules` adds a short note.\n"
            "`s!waiting` names who has not shipped, without the match.\n"
            "`s!finish` archives a drawn event so another can be created.",
        ),
        Field(
            "Suggestions",
            "`/problem` reports a problem and `/idea` requests a feature. "
            "The organizer reads them privately with `s!reports`. "
            "`s!tutorial` walks through the commands when you ask. "
            "`s!news` shows what has changed. That note appears once, then only when something new ships.",
        ),
    )


def welcome_card(
    person_name: object,
    event_name: object,
    rules: str = "",
    past_partners: list | None = None,
) -> Card:
    person = label(person_name, "there")
    event = label(event_name, "Secret Santa")
    fields: list[Field] = []
    note = str(rules or "").strip()
    if note:
        fields.append(Field("Rules for this event", shown(note, "")))
    history = [partner for partner in (past_partners or []) if partner]
    if history:
        names = ", ".join(label(partner.get("name"), "Someone") for partner in history[:25])
        fields.append(
            Field(
                "Past matches in this server",
                f"{names}\nChoose who to skip again on the menu. Anyone you leave off can be drawn. "
                "`s!unexclude` drops someone after you skip them.",
            )
        )
    fields.extend(guide_fields())
    return Card(
        title=f"Welcome to {event}"[:256],
        description=f"{person}, you're in. This is the whole event, from your profile to the gift.",
        fields=tuple(fields),
        footer="s!help lists every command. s!tutorial walks through them. s!info shows this guide again.",
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
        footer="s!tutorial walks through the commands. s!help join shows one command.",
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
        Field("Last day to join", str(payload.get("signup_deadline") or "Not set"), inline=True),
        Field("Gift deadline", str(payload.get("deadline") or "Not set"), inline=True),
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
            Field("Spending limit", spending_text(participant.get("budget"), participant.get("spending_cap"))),
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
    fields = [
        Field("Wishlist", shown(receiver.get("wishlist"), "No wishlist set")),
        Field("Spending limit", spending_text(receiver.get("budget"), receiver.get("spending_cap"))),
        Field("Do not gift", shown(receiver.get("do_not_gift"), "Not set")),
        Field("Notes", shown(receiver.get("notes"), "Not set")),
        Field("Delivery", delivery),
    ]
    rules = str(receiver.get("rules") or "").strip()
    if rules:
        fields.append(Field("Rules", shown(rules, "")))
    return Card(
        title="Your match",
        description=f"You are getting a gift for **{name}**.",
        fields=tuple(fields),
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
        footer="When it goes out, run s!gift shipped. You can add a tracking link.",
    )


def anonymous_card(direction: str, body: str, reply_as: str, image: str = "") -> Card:
    if direction == "giftee":
        title = "A note from the person buying your gift"
        who = "the person buying your gift"
    else:
        title = "A note from the person you're buying for"
        who = "the person you're buying for"
    rendered, found = note_content(body)
    picture = image or found
    if rendered:
        description = rendered
    elif picture:
        description = "Sent a GIF."
    else:
        description = "They sent a note with no text."
    return Card(
        title=title,
        description=description,
        footer=f"Reply with the button, or s!message {reply_as}, to answer {who}. Emoji and GIF links are welcome.",
        image=picture,
    )


TUTORIAL_PAGES: tuple[tuple[str, str], ...] = (
    (
        "Joining",
        "In the server, run `s!join` or `/join`. A guide arrives by DM.\n"
        "`s!help` lists every command. `s!info` shows that guide again. "
        "`s!tutorial` starts this walkthrough whenever you ask.",
    ),
    (
        "Your profile",
        "A wishlist is required. Add a shipping address unless the gift can be digital.\n"
        "`s!profile` opens a private form. `s!setwishlist`, `s!setaddress`, and `s!setbudget` also work, "
        "and those messages are deleted in a server channel. A spending limit is optional. "
        "`s!myinfo` sends your profile by DM.",
    ),
    (
        "The draw",
        "When every profile is complete, the organizer runs `s!start`.\n"
        "You are assigned one person, and someone else is assigned you. "
        "You are not told who is buying for you. The match arrives by DM. `s!match` sends it again.",
    ),
    (
        "Notes and the gift",
        "`s!message giftee` writes to the person you're buying for. "
        "They answer with `s!message gifter` or the Reply button. "
        "Emoji, GIF links, and attached pictures show in the note. `s!notes` DMs the latest copies again. "
        "Keep their name out of the server.\n"
        "Mark the gift one step at a time: `s!gift bought`, then `s!gift shipped`. "
        "Add a tracking link on the shipped step. They mark `s!gift received`.",
    ),
    (
        "If plans change",
        "On `/profile` in the server, skip someone you were paired with before, even if they have not joined. "
        "`s!exclude @someone` does the same. A skip after names are out applies if the matches are drawn again.\n"
        "`s!leave` drops you from the event. After the draw, leaving redraws the open seats "
        "and keeps every match it still can.",
    ),
    (
        "Running it and asking for help",
        "`s!create` opens one event. The organizer is not in the draw until they join. "
        "`s!settings` chooses which reminders go out. `s!status` shows who is missing a profile, not what they wrote.\n"
        "`s!problem` reports a problem. `s!idea` requests a feature. The organizer reads them with `s!reports`. "
        "`s!news` shows what changed.",
    ),
)

_STEP = re.compile(r"Step (\d+) of (\d+)")


def tutorial_page_count() -> int:
    return len(TUTORIAL_PAGES)


def tutorial_card(page: int) -> Card:
    last = len(TUTORIAL_PAGES) - 1
    index = min(max(int(page), 0), last)
    title, body = TUTORIAL_PAGES[index]
    return Card(
        title=f"Tutorial · {title}"[:256],
        description=body,
        footer=f"Step {index + 1} of {len(TUTORIAL_PAGES)}. Use the buttons, or s!tutorial to start over.",
    )


def tutorial_page_from_footer(footer: str) -> int:
    match = _STEP.search(footer or "")
    if match is None:
        return 0
    return max(0, int(match.group(1)) - 1)


TIPS: dict[str, tuple[str, str]] = {
    "profile": (
        "Fill in your profile",
        "A wishlist is required. `/profile` keeps it private. Add an address unless the gift can be digital.",
    ),
    "past-match": (
        "Skip a past match",
        "On `/profile` in the server, the menu skips someone you were paired with before, even if they have not joined.",
    ),
    "notes": (
        "Anonymous notes",
        "Emoji and a GIF link show in the note. The Reply button answers without putting their name in the channel.",
    ),
    "gift-steps": (
        "One step at a time",
        "Mark the gift bought, then shipped. A tracking link can go with the shipped step. "
        "The person receiving it marks `s!gift received`.",
    ),
    "skip-later": (
        "Skips and the current draw",
        "A skip after names are out does not change this draw. It applies if the matches are drawn again.",
    ),
    "privacy": (
        "What status shows",
        "`s!status` shows who is missing a profile. It does not show wishlists, addresses, or pairs.",
    ),
    "organizer": (
        "You are not in the draw yet",
        "Creating the event does not enter you. Join when you want a match. `s!settings` chooses how often reminders go out.",
    ),
    "ask-tutorial": (
        "A walkthrough when you want it",
        "`s!tutorial` walks through the commands one page at a time. It starts only when you ask.",
    ),
}

COMMAND_TIPS = {
    "join": "profile",
    "profile": "past-match",
    "setwishlist": "past-match",
    "setaddress": "past-match",
    "setbudget": "past-match",
    "myinfo": "past-match",
    "notes": "notes",
    "reports": "organizer",
    "message": "notes",
    "match": "notes",
    "partnerinfo": "notes",
    "gift": "gift-steps",
    "exclude": "skip-later",
    "unexclude": "skip-later",
    "status": "privacy",
    "participants": "privacy",
    "create": "organizer",
    "help": "ask-tutorial",
    "info": "ask-tutorial",
}


def tip_id_for(command_name: str) -> str:
    return COMMAND_TIPS.get(command_name or "", "")


def tip_candidates(command_name: str) -> tuple[str, ...]:
    """Tips this command may show, first unseen one only."""
    name = command_name or ""
    if name in {"profile", "setwishlist", "setaddress", "setbudget", "myinfo"}:
        ordered = ("profile", "past-match")
    else:
        primary = tip_id_for(name)
        ordered = (primary,) if primary else ()
    unique: list[str] = []
    for tip_id in ordered:
        if tip_id not in unique:
            unique.append(tip_id)
    return tuple(unique)


def tip_card(tip_id: str) -> Card:
    title, body = TIPS[tip_id]
    return Card(
        title=title,
        description=body,
        footer="This hint shows once. s!tutorial walks through the commands when you ask.",
    )


def changelog_card() -> Card:
    return Card(
        title="What's new",
        description="A quick look at the latest Secret Santa updates.",
        fields=(
            Field(
                "Past matches",
                "When you join, the menu lists people you were paired with in this server before. Choose who to skip.",
            ),
            Field(
                "Still to ship",
                "`s!waiting` DMs the organizer the names of people who have not shipped. The match stays hidden.",
            ),
            Field(
                "Signup date",
                "`s!signupby` sets the last day to join. The gift deadline stays on `s!deadline`.",
            ),
        ),
        footer="This shows once. A new note appears only when something new ships.",
    )


def event_setup_card(cap: object, rules: object) -> Card:
    return Card(
        title="Event setup",
        description="These apply to everyone in this Secret Santa.",
        fields=(
            Field("Spending cap", shown(cap, "Not set")),
            Field("Rules", shown(rules, "Not set")),
        ),
        footer="s!cap and s!rules change these. Use clear to remove one.",
    )


def _person_label(user_id: object) -> str:
    text = str(user_id or "")
    if text.isdigit():
        return f"<@{text}>"
    return "Someone"


def waiting_card(rows: list[dict]) -> Card:
    lines = [
        f"{label(row.get('name'), 'Someone')} — {str(row.get('gift_status') or 'not_bought').replace('_', ' ')}"
        for row in rows
    ]
    if lines:
        description = "\n".join(lines)
    else:
        description = "Everyone has marked the gift shipped, or it has been received."
    return Card(
        title="Still to ship",
        description=description,
        footer="Names only. Their match is not listed. This is sent in a DM.",
    )


def blocks_card(pairs: list[tuple[str, str]]) -> Card:
    lines = [f"{_person_label(left)} and {_person_label(right)}" for left, right in pairs]
    return Card(
        title="Locked pairs",
        description="\n".join(lines),
        footer="They cannot remove these. s!unblock removes one.",
    )


def reports_card(rows: list[dict]) -> Card:
    fields = []
    for row in rows[:10]:
        kind = "Problem" if row.get("kind") == "problem" else "Idea"
        user_id = str(row.get("user_id") or "")
        who = f"<@{user_id}>" if user_id.isdigit() else "Someone"
        fields.append(Field(kind, f"{who}\n{shown(row.get('body'), 'Empty note')}"))
    return Card(
        title="Reports and ideas",
        description="These notes were saved in this server.",
        fields=tuple(fields),
        footer="Only you can see this. It is sent in a DM.",
    )


def notes_card(entries: list[tuple[str, str, str]]) -> Card:
    fields = []
    image = ""
    for title, body, picture in entries:
        rendered, found = note_content(body)
        picture = picture if str(picture).startswith(("http://", "https://")) else ""
        text = rendered or ("Sent a GIF." if picture or found else "They sent a note with no text.")
        if picture and picture not in text:
            text = f"{text}\n{picture}"
        if not image:
            image = picture or found
        fields.append(Field(title, text))
    return Card(
        title="Your latest notes",
        description="These are copies of the latest note in each direction. Names stay out of them.",
        fields=tuple(fields),
        footer="s!message sends a new note. This copy is not posted in the server.",
        image=image if len(entries) == 1 else "",
    )


def shipped_card(tracking: str = "") -> Card:
    link = tracking if str(tracking).startswith(("http://", "https://")) else ""
    fields = (Field("Tracking", link),) if link else ()
    description = "Your gift was marked shipped."
    if not link:
        description += " No tracking link was included."
    return Card(
        title="Your gift is on the way",
        description=description,
        fields=fields,
        footer="When it arrives, run s!gift received.",
    )


_NOTE_TOKEN = re.compile(
    r"(<a?:[A-Za-z0-9_]{2,32}:\d{17,20}>)|(https?://[^\s<]+)",
    re.IGNORECASE,
)
_IMAGE_SUFFIXES = (".gif", ".png", ".jpg", ".jpeg", ".webp")


def note_content(body: str) -> tuple[str, str]:
    """Keep emoji and GIF links intact, and return a direct image URL when there is one."""
    image = ""
    pieces: list[str] = []
    last = 0
    for match in _NOTE_TOKEN.finditer(body or ""):
        pieces.append(safe(body[last:match.start()]))
        emoji, url = match.group(1), match.group(2)
        if emoji:
            pieces.append(emoji)
        else:
            cleaned = url.rstrip(").,]>\"'")
            extra = url[len(cleaned):]
            direct = image_url(cleaned)
            if direct and not image:
                image = direct
            pieces.append(f"<{cleaned}>")
            if extra:
                pieces.append(safe(extra))
        last = match.end()
    pieces.append(safe((body or "")[last:]))
    return "".join(pieces).strip(), image


def image_url(url: str) -> str:
    """Return a URL Discord can show as an image, or an empty string."""
    if not url.startswith(("http://", "https://")):
        return ""
    path = url.lower().split("?", 1)[0].split("#", 1)[0]
    if path.endswith(_IMAGE_SUFFIXES):
        return url
    host_and_path = path.split("://", 1)[-1]
    if host_and_path.startswith(("media.tenor.com/", "media1.tenor.com/", "c.tenor.com/")):
        return url
    giphy = re.search(r"giphy\.com/(?:gifs|media|embed)/([^/?#]+)", url, re.IGNORECASE)
    if giphy:
        gif_id = giphy.group(1).strip("/").rsplit("-", 1)[-1]
        if gif_id:
            return f"https://i.giphy.com/{gif_id}.gif"
    return ""


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
    if card.image.startswith(("http://", "https://")):
        embed.set_image(url=_clip(card.image, 2048))
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
