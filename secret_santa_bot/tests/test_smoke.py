"""One event, driven through the command handlers and stored in Postgres."""

from __future__ import annotations

import random
from datetime import date, timedelta
from itertools import combinations, permutations, product
from types import SimpleNamespace

import psycopg2
import pytest

from bot import app, runtime
from cogs.secret_santa import SecretSantaCog, _match_card
from database import DatabaseManager
from matching import is_derangement
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
    def __init__(self, guild_id: int = 9001):
        self.id = guild_id


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
    assert created.sent[-1]["view"] is not None
    assert "Set up" in plain(created.sent)
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
    ada.command = SimpleNamespace(name="status", cog=cog)
    status_ctx = Ctx(guild, ada)
    status_ctx.command = ada.command
    await cog.cog_after_invoke(status_ctx)
    assert "What's new" in inbox(ada)
    await cog.cog_after_invoke(status_ctx)
    assert "What status shows" in inbox(ada)

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
    await invoke(
        cog,
        SecretSantaCog.setwindow,
        ada,
        text="Away next week. Don't leave it at the door.",
    )

    bea_info = await invoke(cog, SecretSantaCog.myinfo, bea)
    assert "$25 for this event" in _titled(bea, "Your profile")
    assert "Sent in a DM" in plain(bea_info.sent)
    ada_info = await invoke(cog, SecretSantaCog.myinfo, ada)
    assert "$10" in _titled(ada, "Your profile")
    assert "Candles" in _titled(ada, "Your profile")
    assert "for this event" not in _titled(ada, "Your profile")
    assert "Don't leave it at the door" in _titled(ada, "Your profile")
    assert "Sent in a DM" in plain(ada_info.sent)

    started = await invoke(cog, SecretSantaCog.start, org)
    assert "Secret Santa has begun" in plain(started.sent)
    drawn = pairs_of(db, guild.id)
    assert drawn["111"] != "222"
    assert drawn["222"] != "111"
    assert set(drawn) == {"101", "111", "222", "333", "444"}
    assert "Your match" in inbox(ada)
    assert "Handmade gifts only" in inbox(ada)
    ada_giver = next(giver for giver, receiver in drawn.items() if receiver == "111")
    assert "Don't leave it at the door" in inbox(people[int(ada_giver)])

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
    assert "Tutorial ·" not in inbox(ada)
    assert "/profile" in inbox(ada)
    welcomes = [item for item in ada.sent if item.get("embed") and str(item["embed"].title).startswith("Welcome")]
    assert welcomes[-1]["view"] is not None
    assert "welcome is in your DMs" in plain(again.sent)
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


STRESS_GUILD = "stress"


def _service(db, seed: int) -> EventService:
    svc = EventService(db, rng=random.Random(seed))
    if db.get_open_event(STRESS_GUILD) is not None:
        assert svc.cancel(STRESS_GUILD, "org", True).ok
    assert svc.create_event(STRESS_GUILD, "org", "Stress").ok
    assert svc.set_event_copy(STRESS_GUILD, "org", True, "rules", "No cash").ok
    assert svc.set_event_copy(STRESS_GUILD, "org", True, "spending_cap", "$20").ok
    return svc


def _fill(svc: EventService, user_id: str, *, digital: bool, window: bool) -> str:
    assert svc.join(STRESS_GUILD, user_id, f"Person {user_id}").ok
    again = svc.join(STRESS_GUILD, user_id, f"Person {user_id}")
    assert again.ok and "already in" in again.message
    assert svc.set_text_field(STRESS_GUILD, user_id, "wishlist", f"Wish {user_id}", False).ok
    if digital:
        assert svc.update_profile(STRESS_GUILD, user_id, digital_only=True).ok
    else:
        assert svc.set_text_field(STRESS_GUILD, user_id, "address", f"{user_id} North Street", False).ok
    if not window:
        return ""
    text = f"Away-{user_id}-only"
    assert svc.set_text_field(STRESS_GUILD, user_id, "delivery_window", text, False).ok
    return text


def _check_draw(started, user_ids, windows, digital, blocked):
    assert started.ok, started.message
    notes = started.data["notifications"]
    assignment = {note["giver_id"]: note["receiver_id"] for note in notes}
    assert is_derangement(assignment, user_ids, blocked)
    for note in notes:
        receiver = note["receiver_id"]
        card = _match_card(note).text()
        assert f"Wish {receiver}" in card
        assert "No cash" in card
        assert "$20 for this event" in card
        for other, text in windows.items():
            if not text:
                continue
            if other == receiver:
                assert text in card
                assert "Delivery window" in card
            else:
                assert text not in card
        if not windows.get(receiver):
            assert "Delivery window" not in card
        if digital.get(receiver):
            assert "Digital gift" in card
            assert f"{receiver} North Street" not in card
        else:
            assert f"{receiver} North Street" in card
    return assignment


def _close(svc: EventService) -> None:
    if svc.db.get_open_event(STRESS_GUILD) is not None:
        assert svc.cancel(STRESS_GUILD, "org", True).ok


def test_every_joining_combination_draws(db):
    """Every subset of a six-person pool, and every join order up to four people."""
    pool = [f"p{i}" for i in range(6)]
    drawn = 0
    for size in range(2, len(pool) + 1):
        for group in combinations(pool, size):
            svc = _service(db, seed=size + drawn)
            try:
                windows = {}
                digital = {}
                for index, user_id in enumerate(group):
                    digital[user_id] = index % 3 == 0
                    windows[user_id] = _fill(
                        svc,
                        user_id,
                        digital=digital[user_id],
                        window=index % 2 == 1,
                    )
                event = db.get_open_event(STRESS_GUILD)
                assert len(db.list_participants(event["id"])) == size
                started = svc.start(STRESS_GUILD, "org", True)
                _check_draw(started, list(group), windows, digital, set())
                drawn += 1
            finally:
                _close(svc)
    assert drawn == 57

    orders = 0
    for size in range(2, 5):
        for order in permutations([f"o{i}" for i in range(size)]):
            svc = _service(db, seed=1)
            try:
                windows = {}
                digital = {}
                for user_id in order:
                    digital[user_id] = False
                    windows[user_id] = _fill(svc, user_id, digital=False, window=user_id.endswith("1"))
                started = svc.start(STRESS_GUILD, "org", True)
                _check_draw(started, list(order), windows, digital, set())
                orders += 1
            finally:
                _close(svc)
    assert orders == 32

    alone = _service(db, seed=0)
    try:
        _fill(alone, "solo", digital=False, window=True)
        refused = alone.start(STRESS_GUILD, "org", True)
        assert refused.code == "too_small"
        assert db.get_open_event(STRESS_GUILD)["phase"] == "signup"
    finally:
        _close(alone)


def test_every_profile_combination_for_each_small_group(db):
    """Physical or digital, with or without a delivery window, for every person."""
    covered = 0
    for size in range(2, 6):
        for seed, choices in enumerate(product(range(4), repeat=size)):
            svc = _service(db, seed=seed)
            try:
                user_ids = [f"u{i}" for i in range(size)]
                windows = {}
                digital = {}
                for index, choice in enumerate(choices):
                    user_id = user_ids[index]
                    digital[user_id] = choice >= 2
                    windows[user_id] = _fill(
                        svc,
                        user_id,
                        digital=digital[user_id],
                        window=choice % 2 == 1,
                    )
                started = svc.start(STRESS_GUILD, "org", True)
                _check_draw(started, user_ids, windows, digital, set())
                covered += 1
            finally:
                _close(svc)
    assert covered == 16 + 64 + 256 + 1024

    for size in (6, 8, 10, 12):
        patterns = [
            tuple(0 for _ in range(size)),
            tuple(1 for _ in range(size)),
            tuple(2 for _ in range(size)),
            tuple(3 for _ in range(size)),
            tuple(index % 4 for index in range(size)),
        ]
        for index in range(size):
            only_window = [0] * size
            only_window[index] = 1
            patterns.append(tuple(only_window))
            only_digital = [1] * size
            only_digital[index] = 2
            patterns.append(tuple(only_digital))
        for seed, choices in enumerate(patterns):
            svc = _service(db, seed=seed + size)
            try:
                user_ids = [f"b{i}" for i in range(size)]
                windows = {}
                digital = {}
                for index, choice in enumerate(choices):
                    user_id = user_ids[index]
                    digital[user_id] = choice >= 2
                    windows[user_id] = _fill(
                        svc,
                        user_id,
                        digital=digital[user_id],
                        window=choice % 2 == 1,
                    )
                started = svc.start(STRESS_GUILD, "org", True)
                assignment = _check_draw(started, user_ids, windows, digital, set())
                edited = user_ids[0]
                giver = next(person for person, receiver in assignment.items() if receiver == edited)
                updated = svc.set_text_field(
                    STRESS_GUILD,
                    edited,
                    "delivery_window",
                    f"Home-after-{edited}",
                    False,
                )
                assert updated.data["notify_gifter_id"] == giver
                assert updated.data["delivery_window"] == f"Home-after-{edited}"
                cleared = svc.set_text_field(STRESS_GUILD, edited, "delivery_window", "clear", False)
                assert cleared.data["notify_gifter_id"] == giver
                assert cleared.data["delivery_window"] == ""
            finally:
                _close(svc)


def test_locks_and_removals_across_group_sizes(db):
    locked_draws = 0
    for size in range(4, 9):
        people = [f"l{i}" for i in range(size)]
        for left, right in combinations(people, 2):
            svc = _service(db, seed=size)
            try:
                windows = {}
                digital = {}
                for user_id in people:
                    digital[user_id] = False
                    windows[user_id] = _fill(svc, user_id, digital=False, window=user_id.endswith("1"))
                assert svc.block_pair(STRESS_GUILD, "org", True, left, "Left", right, "Right").ok
                blocked = {(left, right), (right, left)}
                started = svc.start(STRESS_GUILD, "org", True)
                assignment = _check_draw(started, people, windows, digital, blocked)
                assert assignment[left] != right and assignment[right] != left
                locked_draws += 1
            finally:
                _close(svc)
    assert locked_draws == 6 + 10 + 15 + 21 + 28

    trio = [f"t{i}" for i in range(3)]
    for left, right in combinations(trio, 2):
        svc = _service(db, seed=3)
        try:
            for user_id in trio:
                _fill(svc, user_id, digital=False, window=False)
            assert svc.block_pair(STRESS_GUILD, "org", True, left, "Left", right, "Right").ok
            assert svc.start(STRESS_GUILD, "org", True).code == "no_draw"
        finally:
            _close(svc)

    for size, block_all in ((2, True), (3, True), (4, True)):
        svc = _service(db, seed=size)
        try:
            people = [f"x{i}" for i in range(size)]
            for user_id in people:
                _fill(svc, user_id, digital=False, window=False)
            if block_all:
                for left, right in combinations(people, 2):
                    assert svc.block_pair(STRESS_GUILD, "org", True, left, "Left", right, "Right").ok
            refused = svc.start(STRESS_GUILD, "org", True)
            assert refused.code == "no_draw"
            assert db.get_open_event(STRESS_GUILD)["phase"] == "signup"
        finally:
            _close(svc)

    for size in range(4, 7):
        svc = _service(db, seed=size)
        try:
            people = [f"y{i}" for i in range(size)]
            for user_id in people:
                _fill(svc, user_id, digital=False, window=False)
            stranded = people[0]
            for other in people[1:]:
                assert svc.block_pair(STRESS_GUILD, "org", True, stranded, "A", other, "B").ok
            assert svc.start(STRESS_GUILD, "org", True).code == "no_draw"
        finally:
            _close(svc)

    pair = _service(db, seed=2)
    try:
        for user_id in ("a", "b"):
            _fill(pair, user_id, digital=False, window=True)
        assert pair.start(STRESS_GUILD, "org", True).ok
        refused = pair.remove(STRESS_GUILD, "org", "a", True)
        assert refused.code == "no_draw"
        assert set(pairs_of(db, STRESS_GUILD)) == {"a", "b"}
    finally:
        _close(pair)

    for size in range(3, 9):
        for remove_at in range(size):
            svc = _service(db, seed=size + remove_at)
            try:
                people = [f"r{i}" for i in range(size)]
                windows = {}
                digital = {user_id: index % 3 == 0 for index, user_id in enumerate(people)}
                for user_id, is_digital in digital.items():
                    windows[user_id] = _fill(svc, user_id, digital=is_digital, window=not is_digital)
                assert svc.start(STRESS_GUILD, "org", True).ok
                target = people[remove_at]
                removed = svc.remove(STRESS_GUILD, "org", target, True)
                assert removed.ok
                remaining = [user_id for user_id in people if user_id != target]
                assignment = pairs_of(db, STRESS_GUILD)
                assert target not in assignment and target not in assignment.values()
                assert is_derangement(assignment, remaining, set())
                for note in removed.data["changed"]:
                    card = _match_card(note).text()
                    receiver = note["receiver_id"]
                    assert receiver != target
                    if windows.get(receiver):
                        assert windows[receiver] in card
                    else:
                        assert "Delivery window" not in card
            finally:
                _close(svc)


def _boot(db, org, people, guild_id):
    guild = Guild(guild_id)

    class Bot:
        def get_user(self, snowflake):
            return people.get(int(snowflake))

        async def fetch_user(self, snowflake):
            return self.get_user(snowflake)

        def get_cog(self, name):
            return cog if name == "SecretSantaCog" else None

    bot = Bot()
    cog = SecretSantaCog.__new__(SecretSantaCog)
    cog.bot = bot
    cog.guild = guild
    cog.service = EventService(db, rng=random.Random(guild_id))
    cog.rate_limiter = RateLimiter()
    return cog, guild, bot


def _match_embed(person: Person) -> str:
    cards = [item for item in person.sent if item.get("embed") is not None and item["embed"].title == "Your match"]
    assert cards, person.display_name
    return plain(cards[-1:])


async def test_command_path_for_each_group_size(db):
    org = Person(7, "Organizer", admin=True)
    for size in range(2, 13):
        roster = [Person(size * 100 + index, f"Member {index}") for index in range(size)]
        people = {person.id: person for person in (org, *roster)}
        cog, guild, bot = _boot(db, org, people, 9100 + size)
        created = await invoke(cog, SecretSantaCog.create, org, name=f"Group of {size}")
        assert f"Group of {size}" in plain(created.sent)
        await invoke(cog, SecretSantaCog.rules, org, text="No cash")
        await invoke(cog, SecretSantaCog.cap, org, text="$20")

        windows = {}
        for index, person in enumerate(roster):
            joined = await invoke(cog, SecretSantaCog.join, person)
            assert "welcome is in your DMs" in plain(joined.sent)
            repeat = await invoke(cog, SecretSantaCog.join, person)
            assert "already in" in plain(repeat.sent).lower()
            welcomes = [
                item
                for item in person.sent
                if item.get("embed") is not None and str(item["embed"].title).startswith("Welcome")
            ]
            assert len(welcomes) == 1
            await invoke(cog, SecretSantaCog.setwishlist, person, wishlist=f"Wish {person.id}")
            if index % 3 == 0:
                assert cog.service.update_profile(str(guild.id), str(person.id), digital_only=True).ok
                windows[str(person.id)] = ""
            else:
                await invoke(cog, SecretSantaCog.setaddress, person, address=f"{person.id} North Street")
                if index % 2 == 1:
                    text = f"Away-{person.id}-only"
                    await invoke(cog, SecretSantaCog.setwindow, person, text=text)
                    windows[str(person.id)] = text
                else:
                    windows[str(person.id)] = ""

        if size >= 5:
            await invoke(cog, SecretSantaCog.block, org, roster[0], roster[1])

        started = await invoke(cog, SecretSantaCog.start, org)
        assert "Secret Santa has begun" in plain(started.sent)
        assignment = pairs_of(db, guild.id)
        blocked = set()
        if size >= 5:
            blocked = {(str(roster[0].id), str(roster[1].id)), (str(roster[1].id), str(roster[0].id))}
        user_ids = [str(person.id) for person in roster]
        assert is_derangement(assignment, user_ids, blocked)
        for receiver_id, window in windows.items():
            giver = people[int(next(person for person, receiver in assignment.items() if receiver == receiver_id))]
            card = _match_embed(giver)
            assert f"Wish {receiver_id}" in card
            assert "No cash" in card
            assert "$20 for this event" in card
            if window:
                assert window in card
            else:
                assert "Delivery window" not in card
            if int(receiver_id) % 100 % 3 == 0:
                assert "Digital gift" in card
            else:
                assert f"{receiver_id} North Street" in card

        window_owner = next(person for person in roster if windows[str(person.id)])
        new_window = f"Home-after-{window_owner.id}"
        await invoke(cog, SecretSantaCog.setwindow, window_owner, text=new_window)
        giver = people[int(next(person for person, receiver in assignment.items() if receiver == str(window_owner.id)))]
        assert new_window in inbox(giver)
        assert "Your giftee updated their delivery window" in inbox(giver)

        if size == 2:
            refused = await invoke(cog, SecretSantaCog.remove, org, roster[0])
            assert "nobody was removed" in plain(refused.sent).lower()
            assert set(pairs_of(db, guild.id)) == {str(person.id) for person in roster}
        else:
            target = roster[-1]
            await invoke(cog, SecretSantaCog.remove, org, target)
            assert "You were removed" in inbox(target)
            remaining = pairs_of(db, guild.id)
            assert str(target.id) not in remaining and str(target.id) not in remaining.values()
            assert is_derangement(remaining, [user_id for user_id in user_ids if user_id != str(target.id)], blocked)

        await invoke(cog, SecretSantaCog.cancel, org)
        assert db.get_open_event(str(guild.id)) is None
