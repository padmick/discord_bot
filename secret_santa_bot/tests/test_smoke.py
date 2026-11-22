"""One event, driven through the command handlers and stored in Postgres."""

from __future__ import annotations

import random
from datetime import date, timedelta
from types import SimpleNamespace

import psycopg2
import pytest

from bot import app, runtime
from cogs.secret_santa import SecretSantaCog
from database import DatabaseManager
from services import EventService, RateLimiter
from views import ReplyModal


@pytest.fixture
def db(postgres_url):
    """Start from an empty database.

    Migration tests earlier in the suite drop the public schema and leave it
    partway through an upgrade. This smoke needs every table.
    """
    connection = psycopg2.connect(postgres_url)
    connection.autocommit = True
    with connection.cursor() as cursor:
        cursor.execute("DROP SCHEMA public CASCADE")
        cursor.execute("CREATE SCHEMA public")
    connection.close()
    manager = DatabaseManager(postgres_url)
    yield manager
    manager.close()


class Guild:
    def __init__(self):
        self.id = 9001


class Person:
    def __init__(self, user_id: int, name: str, admin: bool = False):
        self.id = user_id
        self.display_name = name
        self.bot = False
        self.guild_permissions = SimpleNamespace(administrator=admin)
        self.sent: list[dict] = []

    async def send(self, content=None, **kwargs):
        self.sent.append({"content": content, "embed": kwargs.get("embed"), "view": kwargs.get("view")})


class Ctx:
    def __init__(self, guild, author, message=None):
        self.guild = guild
        self.author = author
        self.interaction = None
        self.channel = self
        self.message = message
        self.sent: list[dict] = []
        self.command = SimpleNamespace(name="join", cog=None)
        self.command_failed = False

    async def send(self, content=None, **kwargs):
        self.sent.append({"content": content, "embed": kwargs.get("embed"), "view": kwargs.get("view")})


class Response:
    def __init__(self):
        self.done = False
        self.modals = []
        self.messages = []

    def is_done(self):
        return self.done

    async def defer(self, ephemeral=False):
        del ephemeral
        self.done = True

    async def send_message(self, content=None, **kwargs):
        self.done = True
        self.messages.append((content, kwargs))

    async def send_modal(self, modal):
        self.done = True
        self.modals.append(modal)

    async def edit_message(self, **kwargs):
        self.done = True
        self.messages.append(kwargs)


class Followup:
    def __init__(self):
        self.messages = []

    async def send(self, content=None, **kwargs):
        self.messages.append((content, kwargs))


class Interaction:
    def __init__(self, user, guild, client):
        self.user = user
        self.guild = guild
        self.guild_id = guild.id
        self.client = client
        self.response = Response()
        self.followup = Followup()
        self.message = None


class Message:
    def __init__(self, attachments):
        self.attachments = attachments

    async def delete(self):
        return None


class Attachment:
    def __init__(self, url: str):
        self.content_type = "image/gif"
        self.filename = "wave.gif"
        self.url = url


def _titled(person: Person, title: str) -> str:
    chosen = [item for item in person.sent if item.get("embed") is not None and item["embed"].title == title]
    return plain(chosen)


def _images(person: Person) -> str:
    urls = []
    for item in person.sent:
        embed = item.get("embed")
        image = getattr(embed, "image", None) if embed is not None else None
        url = getattr(image, "url", None)
        if url:
            urls.append(url)
    return "\n".join(urls)


def plain(sent) -> str:
    chunks = []
    for item in sent:
        if item.get("content"):
            chunks.append(str(item["content"]))
        embed = item.get("embed")
        if embed is None:
            continue
        chunks.append(embed.title or "")
        chunks.append(embed.description or "")
        for field in embed.fields:
            chunks.append(f"{field.name}: {field.value}")
    return "\n".join(chunks)


def inbox(person: Person) -> str:
    return plain(person.sent)


async def invoke(cog, command, author, *args, message=None, **kwargs):
    ctx = Ctx(cog.guild, author, message=message)
    ctx.command.cog = cog
    await command.callback(cog, ctx, *args, **kwargs)
    return ctx


def pairs_of(db, guild_id) -> dict[str, str]:
    event = db.get_open_event(str(guild_id))
    return {row["giver_id"]: row["receiver_id"] for row in db.list_pairings(event["id"])}


async def test_one_event_exercises_every_feature(db):
    guild = Guild()
    org = Person(101, "Organizer", admin=True)
    ada = Person(111, "Ada")
    bea = Person(222, "Bea")
    cara = Person(333, "Cara")
    dee = Person(444, "Dee")
    eli = Person(555, "Eli")
    eve = Person(666, "Eve")
    people = {person.id: person for person in (org, ada, bea, cara, dee, eli, eve)}

    class Bot:
        def __init__(self):
            self.cog = None

        def get_user(self, snowflake):
            return people.get(int(snowflake))

        async def fetch_user(self, snowflake):
            return self.get_user(snowflake)

        def get_cog(self, name):
            return self.cog if name == "SecretSantaCog" else None

    bot = Bot()
    cog = SecretSantaCog.__new__(SecretSantaCog)
    cog.bot = bot
    cog.guild = guild
    cog.service = EventService(db, rng=random.Random(7))
    cog.rate_limiter = RateLimiter()
    bot.cog = cog

    deadline = (date.today() + timedelta(days=2)).isoformat()
    created = await invoke(cog, SecretSantaCog.create, org, name="Winter Party")
    assert "Winter Party" in plain(created.sent)
    rules = await invoke(cog, SecretSantaCog.rules, org, text="Handmade gifts only")
    assert "Handmade gifts only" in plain(rules.sent)
    cap = await invoke(cog, SecretSantaCog.cap, org, text="$25")
    assert "$25" in plain(cap.sent)
    shown = await invoke(cog, SecretSantaCog.rules, org, text="")
    assert "Handmade gifts only" in plain(shown.sent)
    assert "$25" in plain(shown.sent)
    await invoke(cog, SecretSantaCog.deadline, org, deadline)
    signup = await invoke(cog, SecretSantaCog.signupby, org, day=deadline)
    assert "through that day" in plain(signup.sent).lower()

    settings = await invoke(cog, SecretSantaCog.settings, org)
    settings_view = settings.sent[-1]["view"]
    settings_form = Interaction(org, guild, bot)
    await settings_view.edit.callback(settings_form)
    modal = settings_form.response.modals[0]
    modal.profile_reminders._value = "on"
    modal.shipping_reminders._value = "on"
    modal.interval_days._value = "1"
    modal.lead_days._value = "3"
    saved = Interaction(org, guild, bot)
    await modal.on_submit(saved)
    stored = db.get_open_event(str(guild.id))
    assert stored["reminder_interval_days"] == 1
    assert stored["shipping_lead_days"] == 3

    await invoke(cog, SecretSantaCog.lock, org)
    closed = await invoke(cog, SecretSantaCog.join, eli)
    assert "closed" in plain(closed.sent).lower()
    await invoke(cog, SecretSantaCog.unlock, org)

    for person in (ada, bea, cara, dee, org, eli):
        await invoke(cog, SecretSantaCog.join, person)
    assert "Rules for this event" in inbox(ada)
    assert "Handmade gifts only" in inbox(ada)
    ada.command = SimpleNamespace(name="join", cog=cog)
    join_ctx = Ctx(guild, ada)
    join_ctx.command = ada.command
    await cog.cog_after_invoke(join_ctx)
    assert "What's new" in inbox(ada)
    await cog.cog_after_invoke(join_ctx)
    assert "Fill in your profile" in inbox(ada)

    await invoke(cog, SecretSantaCog.leave, eli)
    roster = await invoke(cog, SecretSantaCog.participants, org)
    assert "Eli" not in plain(roster.sent)
    assert "Ada" in plain(roster.sent)

    await invoke(cog, SecretSantaCog.exclude, ada, eve)
    await invoke(cog, SecretSantaCog.unexclude, ada, eve)
    profile = await invoke(cog, SecretSantaCog.profile, ada)
    profile_view = profile.sent[-1]["view"]
    menu = next(item for item in profile_view.children if hasattr(item, "values"))
    menu._values = [eve]
    picked = Interaction(ada, guild, bot)
    await menu.callback(picked)
    assert "won't be paired" in picked.followup.messages[-1][0]
    await invoke(cog, SecretSantaCog.unexclude, ada, eve)

    await invoke(cog, SecretSantaCog.block, org, ada, bea)
    locked = await invoke(cog, SecretSantaCog.blocks, org)
    assert "<@111>" in inbox(org) and "<@222>" in inbox(org)
    assert "Locked pairs" in plain(locked.sent) or "Locked pairs" in inbox(org)
    refused = await invoke(cog, SecretSantaCog.unexclude, ada, bea)
    assert "organizer" in plain(refused.sent).lower()

    waiting = await invoke(cog, SecretSantaCog.status, ada)
    assert "Ada" in plain(waiting.sent) or "wishlist" in plain(waiting.sent).lower()
    reminded = await invoke(cog, SecretSantaCog.remind, org)
    assert "Sent reminders" in plain(reminded.sent)
    assert "Your profile is still incomplete" in inbox(ada)
    await SecretSantaCog.daily_reminders.coro(cog)
    assert db.get_open_event(str(guild.id))["last_profile_reminder"] == date.today()

    early = await invoke(cog, SecretSantaCog.start, org)
    assert "Cannot start" in plain(early.sent)
    assert "The draw is waiting on you" in inbox(bea)

    ada_form = Interaction(ada, guild, bot)
    await profile_view.edit.callback(ada_form)
    ada_modal = ada_form.response.modals[0]
    ada_modal.wishlist._value = "Books and tea"
    ada_modal.budget._value = "$10"
    ada_modal.do_not_gift._value = "Candles"
    ada_modal.notes._value = "No strong perfume"
    ada_modal.address._value = "1 Ada Street"
    await ada_modal.on_submit(Interaction(ada, guild, bot))

    await invoke(cog, SecretSantaCog.setwishlist, bea, wishlist="Wool scarf")
    await invoke(cog, SecretSantaCog.setaddress, bea, address="2 Bea Road")
    cara_profile = await invoke(cog, SecretSantaCog.profile, cara)
    cara_view = cara_profile.sent[-1]["view"]
    await cara_view.toggle_digital.callback(Interaction(cara, guild, bot))
    await invoke(cog, SecretSantaCog.setwishlist, cara, wishlist="A song")
    await invoke(cog, SecretSantaCog.setwishlist, dee, wishlist="Puzzle")
    await invoke(cog, SecretSantaCog.setaddress, dee, address="4 Dee Lane")
    await invoke(cog, SecretSantaCog.setwishlist, org, wishlist="Notebook")
    await invoke(cog, SecretSantaCog.setaddress, org, address="9 Organizer Way")
    await invoke(cog, SecretSantaCog.setbudget, org, budget="$40")

    bea_info = await invoke(cog, SecretSantaCog.myinfo, bea)
    assert "$25 for this event" in _titled(bea, "Your profile")
    assert "Sent in a DM" in plain(bea_info.sent)
    ada_info = await invoke(cog, SecretSantaCog.myinfo, ada)
    assert "$10" in _titled(ada, "Your profile")
    assert "Candles" in _titled(ada, "Your profile")
    assert "for this event" not in _titled(ada, "Your profile")
    assert "Sent in a DM" in plain(ada_info.sent)

    started = await invoke(cog, SecretSantaCog.start, org)
    assert "Secret Santa has begun" in plain(started.sent)
    drawn = pairs_of(db, guild.id)
    assert drawn["111"] != "222"
    assert drawn["222"] != "111"
    assert set(drawn) == {"101", "111", "222", "333", "444"}
    assert "Your match" in inbox(ada)
    assert "Handmade gifts only" in inbox(ada)

    bea_giver = next(giver for giver, receiver in drawn.items() if receiver == "222")
    giver_person = people[int(bea_giver)]
    await invoke(cog, SecretSantaCog.match, giver_person)
    assert "$25 for this event" in inbox(giver_person)
    await invoke(cog, SecretSantaCog.partnerinfo, ada)

    await SecretSantaCog.daily_reminders.coro(cog)
    assert "Your gift is not marked shipped" in inbox(ada)
    assert db.get_open_event(str(guild.id))["last_shipping_reminder"] == date.today()

    picture = Message([Attachment("https://cdn.example/wave.gif")])
    sent_note = await invoke(cog, SecretSantaCog.message, ada, "giftee", text="Hello 🎁", message=picture)
    assert "Sent." in plain(sent_note.sent)
    giftee = people[int(drawn["111"])]
    assert "Hello" in inbox(giftee)
    assert "https://cdn.example/wave.gif" in _images(giftee)

    match_dm = next(item for item in ada.sent if item.get("embed") and item["embed"].title == "Your match")
    reply_button = match_dm["view"].children[0]
    opener = Interaction(ada, guild, bot)
    await reply_button.callback(opener)
    reply = opener.response.modals[0]
    assert isinstance(reply, ReplyModal)
    reply.body._value = "One more note 🎁"
    posted = Interaction(ada, guild, bot)
    await reply.on_submit(posted)
    assert any("Sent." in (item[0] or "") for item in posted.response.messages + posted.followup.messages)

    back = await invoke(cog, SecretSantaCog.message, giftee, "gifter", text="Thank you 🎁")
    assert "Sent." in plain(back.sent)
    notes = await invoke(cog, SecretSantaCog.notes, ada)
    assert "One more note" in inbox(ada)
    assert "Thank you" in inbox(ada)
    assert "To the person you're buying for" in inbox(ada)
    assert "Sent in a DM" in plain(notes.sent)

    await invoke(cog, SecretSantaCog.gift, ada, "bought")
    shipped = await invoke(cog, SecretSantaCog.gift, ada, "shipped", link="https://track.example/parcel")
    assert "shipped" in plain(shipped.sent).lower()
    ada_giftee = people[int(drawn["111"])]
    assert "https://track.example/parcel" in inbox(ada_giftee)
    received = await invoke(cog, SecretSantaCog.gift, ada_giftee, "received")
    assert "received" in plain(received.sent).lower()
    assert pairs_of(db, guild.id)
    ada_row = next(row for row in db.list_pairings(db.get_open_event(str(guild.id))["id"]) if row["giver_id"] == "111")
    assert ada_row["gift_status"] == "received"
    assert ada_row["tracking_url"] == "https://track.example/parcel"
    pending = await invoke(cog, SecretSantaCog.waiting, org)
    still = _titled(org, "Still to ship")
    assert "not bought" in still
    assert "Ada" not in still
    assert "Sent in a DM" in plain(pending.sent)

    changed = await invoke(cog, SecretSantaCog.setwishlist, ada_giftee, wishlist="Books, tea, and honey")
    assert "Profile saved" in inbox(ada_giftee) or "Profile saved" in plain(changed.sent)
    assert "Your giftee updated their wishlist" in inbox(ada)

    await invoke(cog, SecretSantaCog.problem, bea, text="The button label is tiny")
    await invoke(cog, SecretSantaCog.idea, bea, text="Add a gift photo")
    report = await invoke(cog, SecretSantaCog.reports, org)
    assert "The button label is tiny" in inbox(org)
    assert "Add a gift photo" in inbox(org)
    assert "Sent in a DM" in plain(report.sent)

    announced = await invoke(cog, SecretSantaCog.broadcast, org, text="Wrapping night is Friday")
    assert "Sent the announcement" in plain(announced.sent)
    assert "Wrapping night is Friday" in inbox(cara)

    news = await invoke(cog, SecretSantaCog.news, cara)
    assert "What's new" in plain(news.sent)
    tutorial = await invoke(cog, SecretSantaCog.tutorial, cara)
    tutorial_view = tutorial.sent[-1]["view"]
    nxt = next(button for button in tutorial_view.children if "next" in (button.custom_id or ""))
    page = Interaction(cara, guild, bot)
    page.message = SimpleNamespace(embeds=tutorial.sent[-1]["embed"] and [tutorial.sent[-1]["embed"]])
    await nxt.callback(page)
    assert page.response.messages

    info = await invoke(cog, SecretSantaCog.info, ada)
    assert "Your profile" in plain(info.sent)
    logs = await invoke(cog, SecretSantaCog.logs, org)
    assert "log" in (plain(logs.sent) + inbox(org)).lower()
    limits = await invoke(cog, SecretSantaCog.ratelimit, org, "status")
    assert "Rate limiting is" in plain(limits.sent)
    await invoke(cog, SecretSantaCog.ratelimit, org, "commands", 8)
    await invoke(cog, SecretSantaCog.ratelimit, org, "window", 30)
    await invoke(cog, SecretSantaCog.ratelimit, org, "off")
    assert cog.rate_limiter.max_commands == 8
    assert cog.rate_limiter.window == 30
    assert cog.rate_limiter.enabled is False

    revealed = await invoke(cog, SecretSantaCog.reveal, org, "reveal")
    assert "Anonymity is broken" in plain(revealed.sent) or "Anonymity is broken" in inbox(org)
    assert "Ada" in inbox(org)

    await invoke(cog, SecretSantaCog.unblock, org, ada, bea)
    cleared = await invoke(cog, SecretSantaCog.blocks, org)
    assert "No organizer locks" in plain(cleared.sent)

    removed = await invoke(cog, SecretSantaCog.remove, org, dee)
    assert "removed" in plain(removed.sent).lower()
    assert "You were removed" in inbox(dee)
    remaining = pairs_of(db, guild.id)
    assert "444" not in remaining and "444" not in remaining.values()
    assert len(remaining) == 4

    finished = await invoke(cog, SecretSantaCog.finish, org)
    assert "finished" in plain(finished.sent).lower()
    await invoke(cog, SecretSantaCog.create, org, name="Next year")
    carried = await invoke(cog, SecretSantaCog.carrylocks, org)
    assert "no organizer locks" in plain(carried.sent).lower()
    again = await invoke(cog, SecretSantaCog.join, ada)
    assert "Past matches in this server" in inbox(ada)
    assert any(item.get("view") is not None for item in ada.sent)
    assert "guide is in your DMs" in plain(again.sent)
    cancelled = await invoke(cog, SecretSantaCog.cancel, org)
    assert "cancelled" in plain(cancelled.sent).lower()
    assert db.get_open_event(str(guild.id)) is None

    previous = runtime["db"]
    runtime["db"] = db
    try:
        response = app.test_client().get("/health")
        assert response.status_code == 200
        assert response.get_json()["database"] is True
        assert response.get_json()["status"] == "healthy"
    finally:
        runtime["db"] = previous
