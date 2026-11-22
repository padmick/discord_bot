"""Cards for the welcome DM, grouped help, and the messages people read."""

from datetime import date

from cogs.secret_santa import CustomHelpCommand, SecretSantaCog
from present import (
    announcement_card,
    anonymous_card,
    Card,
    CHANGELOG_VERSION,
    changelog_card,
    image_url,
    note_content,
    profile_prompt_card,
    help_card,
    tip_card,
    tip_candidates,
    tip_id_for,
    tutorial_card,
    tutorial_page_count,
    tutorial_page_from_footer,
    info_card,
    match_card,
    notes_card,
    profile_card,
    reports_card,
    profile_reminder_card,
    reveal_card,
    reveal_cards,
    roster_card,
    shipped_card,
    shipping_reminder_card,
    status_card,
    to_embed,
    wishlist_update_card,
    welcome_card,
)
from services import EventService, assignment_text


class _Memory:
    def __init__(self):
        self.events = {}
        self.participants = {}
        self._next = 1

    def get_open_event(self, guild_id):
        for event in self.events.values():
            if event["guild_id"] == guild_id and event["phase"] != "done":
                return event
        return None

    def create_event(self, guild_id, name, organizer_id):
        event = {
            "id": self._next,
            "guild_id": guild_id,
            "name": name,
            "phase": "signup",
            "organizer_id": organizer_id,
        }
        self.events[event["id"]] = event
        self.participants[event["id"]] = {}
        self._next += 1
        return event

    def add_participant(self, event_id, user_id, name):
        self.participants[event_id][user_id] = {"user_id": user_id, "name": name}


def test_welcome_explains_the_event_and_neutralizes_mentions():
    card = welcome_card("@everyone", "Winter *Party*")
    text = card.text()
    assert "@everyone" not in text
    assert "Winter" in text
    for topic in (
        "wishlist",
        "digital",
        "s!start",
        "s!message giftee",
        "s!gift bought",
        "s!reveal reveal",
        "s!exclude",
        "have not joined",
    ):
        assert topic in text
    assert card.title.startswith("Welcome to ")
    embed = to_embed(card)
    assert len(embed.title) <= 256
    assert all(len(field.value) <= 1024 for field in embed.fields)
    assert len(embed.fields) <= 25


def test_profile_prompt_points_at_the_server_menu():
    guild = profile_prompt_card(True).text().lower()
    assert "menu" in guild
    assert "do not have to have joined" in guild
    direct = profile_prompt_card(False).text().lower()
    assert "run /profile in the server" in direct


def test_past_matches_are_grouped_with_getting_started():
    text = help_card(
        [("exclude", "Skip a past match"), ("gift", "Mark the gift")],
        "s!",
    ).text()
    assert text.index("Getting started") < text.index("`s!exclude`") < text.index("After the draw")
    assert text.index("`s!exclude`") < text.index("`s!gift`")


def test_info_uses_the_same_guide_as_the_welcome():
    welcome = {field.name for field in welcome_card("Ada", "Party").fields}
    info = info_card()
    assert welcome == {field.name for field in info.fields}
    assert "s!join" in info.footer


def test_help_groups_commands_and_keeps_each_field_short():
    entries = [(name, "x" * 80) for name in (
        "help", "info", "join", "leave", "profile", "setwishlist", "setaddress", "myinfo",
        "match", "message", "gift", "create", "start", "settings", "reveal", "custom",
    )]
    card = help_card(entries, "/")
    text = card.text()
    assert text.index("Getting started") < text.index("`/join`")
    assert text.index("After the draw") < text.index("`/match`")
    assert text.index("Organizer") < text.index("`/start`")
    assert "Other" in text and "`/custom`" in text
    assert "exclude" not in text
    embed = to_embed(card)
    assert all(len(field.value) <= 1024 for field in embed.fields)
    assert embed.fields


def test_command_descriptions_fit_slash_limits():
    for command in SecretSantaCog.__cog_commands__:
        assert 1 <= len(command.short_doc) <= 100, command.name
    helper = CustomHelpCommand()
    assert helper.command_attrs["help"] == "Show commands grouped by what you are doing"
    commands_by_name = {command.name: command for command in SecretSantaCog.__cog_commands__}
    assert {"problem", "idea", "news", "tutorial", "setbudget", "notes", "reports"} <= set(commands_by_name)
    assert "include" in commands_by_name["unexclude"].aliases
    assert not any("guild_only" in getattr(check, "__qualname__", "") for check in commands_by_name["status"].checks)


def test_profile_match_and_status_keep_private_text_in_the_right_card():
    profile = profile_card(
        {
            "wishlist": "books",
            "address": "99 Private Road",
            "do_not_gift": "socks",
            "notes": "no candles",
            "digital_only": False,
        }
    )
    assert "99 Private Road" in profile.text()
    assert "Spending limit" in profile.text()
    assert "shirt" not in profile.text().lower()
    match = match_card({"name": "@here", "wishlist": "books", "digital_only": True})
    assert "@here" not in match.text()
    assert "Digital gift. No shipping address." in match.text()
    assert "s!message giftee" in match.text()
    status = status_card(
        {
            "name": "Party",
            "phase": "drawn",
            "deadline": None,
            "count": 2,
            "missing_wishlist": [],
            "missing_address": ["Ada"],
            "gift_counts": {"not_bought": 1, "bought": 1, "shipped": 0, "received": 0},
        }
    )
    rendered = status.text()
    assert "Party is in the drawn phase." in rendered
    assert "Missing an address: Ada" in rendered
    assert "1 bought" in rendered
    assert "books" not in rendered


def test_roster_reminders_notes_and_reveal_layout():
    assert roster_card("Party", []).description == "Nobody has joined yet."
    assert "1. Ada" in roster_card("Party", ["Ada"]).text()
    reminder = profile_reminder_card(["wishlist", "address"])
    assert "wishlist, address" in reminder.text()
    assert "/profile" in reminder.text()
    shipping = shipping_reminder_card(date(2026, 12, 20))
    assert "2026-12-20" in shipping.text()
    assert "s!gift shipped" in shipping.footer
    note = anonymous_card("giftee", "@everyone hello", "gifter")
    assert note.title == "A note from the person buying your gift"
    assert "Secret Santa" not in note.title
    assert "@everyone" not in note.text()
    assert "s!message gifter" in note.footer
    assert "person buying your gift" in note.footer
    emoji = "<:party_hat:123456789012345678>"
    rendered, found = note_content(f"Hello {emoji} and hello_world")
    assert emoji in rendered
    assert "hello\\_world" in rendered
    assert found == ""
    gif_note = anonymous_card("giftee", "https://giphy.com/gifs/cats-abc123", "gifter")
    assert gif_note.image == "https://i.giphy.com/abc123.gif"
    assert "https://giphy.com/gifs/cats-abc123" in gif_note.description
    assert to_embed(gif_note).image.url == gif_note.image
    attached = anonymous_card("gifter", "", "giftee", "https://cdn.discordapp.com/attachments/1/2/note.gif?ex=1")
    assert attached.title == "A note from the person you're buying for"
    assert "s!message giftee" in attached.footer
    assert attached.description == "Sent a GIF."
    assert attached.image.endswith("note.gif?ex=1")
    assert image_url("https://tenor.com/view/cat-gif-12345") == ""
    assert image_url("https://media.tenor.com/abc/cat") == "https://media.tenor.com/abc/cat"
    assert image_url("https://giphy.com/embed/xyz") == "https://i.giphy.com/xyz.gif"
    assert image_url("not a url") == ""
    rendered, picture = note_content(
        "See https://cdn.example/a.gif, then https://cdn.example/b.png and <a:spark:123456789012345678>."
    )
    assert picture == "https://cdn.example/a.gif"
    assert "<https://cdn.example/b.png>" in rendered
    assert "<a:spark:123456789012345678>" in rendered
    assert "cdn.example/b.png" not in picture
    blank = to_embed(Card(title="Plain", image="javascript:alert(1)"))
    assert not blank.image.url
    assert announcement_card("  ").description == "The organizer sent an empty announcement."
    assert "books" in wishlist_update_card("books").text()
    assert note_content("A gift 🎁")[0] == "A gift 🎁"
    first = tutorial_card(0)
    assert first.title == "Tutorial · Joining"
    assert "Step 1 of" in first.footer
    assert tutorial_page_from_footer(first.footer) == 0
    last = tutorial_card(tutorial_page_count() + 5)
    assert tutorial_page_from_footer(last.footer) == tutorial_page_count() - 1
    assert tutorial_card(-3).title == first.title
    assert tip_id_for("join") == "profile"
    assert tip_candidates("profile") == ("profile", "past-match")
    assert tip_candidates("join") == ("profile",)
    assert tip_id_for("setwishlist") == "past-match"
    assert tip_id_for("message") == tip_id_for("match")
    assert tip_id_for("tutorial") == ""
    assert tip_id_for("leave") == ""
    hint = tip_card("notes")
    assert "GIF" in hint.text()
    assert "shows once" in hint.footer
    news = changelog_card()
    assert CHANGELOG_VERSION == 2
    assert "What's new" in news.title
    assert "s!setbudget" in news.text()
    assert "s!notes" in news.text()
    assert "s!reports" in news.text()
    copied = notes_card([("To the person you're buying for", "hello @Ada", "https://cdn.example/a.gif")])
    assert "@Ada" not in copied.text()
    assert "hello" in copied.text()
    assert copied.image.endswith("a.gif")
    reports = reports_card([{"kind": "problem", "user_id": "42", "body": "the button failed"}])
    assert "<@42>" in reports.text()
    assert "https://track.example/1" in shipped_card("https://track.example/1").text()
    assert "No tracking link" in shipped_card("").text()
    draw = reveal_card([{"giver_name": "Ada", "receiver_name": "Bea"}])
    assert "Ada -> Bea" in draw.text()
    assert "not posted in the server" in draw.footer
    pages = reveal_cards(
        [{"giver_name": f"Person {index}", "receiver_name": "x" * 80} for index in range(80)]
    )
    assert len(pages) > 1
    combined = "\n".join(page.description for page in pages)
    assert "Person 0" in combined
    assert "Person 79" in combined
    crowded = status_card(
        {
            "name": "Party",
            "phase": "signup",
            "deadline": None,
            "count": 40,
            "missing_wishlist": [f"Person {index} with a long display name" for index in range(40)],
            "missing_address": [],
            "gift_counts": None,
        }
    )
    missing = next(field.value for field in crowded.fields if field.name == "Missing a wishlist")
    assert len(missing) <= 1000
    assert "and " in missing and missing.endswith("more")


def test_join_attaches_the_welcome_without_posting_it_as_the_channel_card():
    database = _Memory()
    service = EventService(database)
    service.db.create_event("guild", "Party", "org")
    joined = service.join("guild", "ada", "Ada")
    assert joined.ok
    assert joined.card is None
    assert "Ada" in joined.data["welcome"].text()
    assert "Party" in joined.data["welcome"].text()
    assert joined.message == "**Ada** joined **Party**."


async def test_channel_reply_embeds_a_card_and_keeps_plain_text_plain():
    from cogs.secret_santa import _show
    from present import Card

    class Ctx:
        interaction = None

        def __init__(self):
            self.sent = []

        async def send(self, content=None, **kwargs):
            self.sent.append((content, kwargs))

    ctx = Ctx()
    await _show(ctx, type("Result", (), {"card": Card(title="Hello", description="There"), "message": "ignored"})())
    assert ctx.sent[0][0] is None
    assert ctx.sent[0][1]["embed"].title == "Hello"

    plain = Ctx()
    await _show(plain, type("Result", (), {"card": None, "message": "Just text"})())
    assert plain.sent == [("Just text", {})]


def test_assignment_text_matches_the_card_and_long_text_is_clipped():
    note = {
        "receiver_name": "Bea",
        "wishlist": "books",
        "address": "1 Road",
        "do_not_gift": "",
        "notes": "",
        "digital_only": False,
    }
    rendered = assignment_text(note)
    assert "Bea" in rendered
    assert "books" in rendered
    assert "s!message giftee" in rendered
    embed = to_embed(match_card({"name": "Bea", "wishlist": "y" * 5000, "digital_only": False}))
    assert len(embed.fields[0].value) <= 1024
    assert embed.fields[0].value.endswith("…")


async def test_tutorial_buttons_move_one_page_and_stop_at_the_ends():
    from types import SimpleNamespace

    from views import TutorialView

    start = TutorialView(0)
    assert start.children[0].disabled
    assert not start.children[1].disabled
    edited = {}

    class Response:
        async def edit_message(self, **kwargs):
            edited.update(kwargs)

    interaction = SimpleNamespace(
        message=SimpleNamespace(embeds=[to_embed(tutorial_card(0))]),
        response=Response(),
    )
    await start.children[1].callback(interaction)
    assert "Step 2 of" in edited["embed"].footer.text
    assert edited["view"].children[0].disabled is False

    end = TutorialView(tutorial_page_count() - 1)
    assert end.children[1].disabled
    edited.clear()
    interaction.message = SimpleNamespace(embeds=[to_embed(tutorial_card(tutorial_page_count() - 1))])
    await end.children[0].callback(interaction)
    assert f"Step {tutorial_page_count() - 1} of" in edited["embed"].footer.text

