"""One-time notes, attachments, and long replies."""

from types import SimpleNamespace

import discord

from cogs.secret_santa import SecretSantaCog, _attached_image, _delete_invocation, _send_chunks
from services import EventService


class _MemoryDB:
    def __init__(self):
        self.seen_changelog = {}
        self.seen_tips = set()

    def claim_changelog(self, user_id, version):
        user_id = str(user_id)
        if self.seen_changelog.get(user_id, 0) >= int(version):
            return False
        self.seen_changelog[user_id] = int(version)
        return True

    def claim_tip(self, user_id, tip_id):
        key = (str(user_id), tip_id)
        if key in self.seen_tips:
            return False
        self.seen_tips.add(key)
        return True


def _cog():
    cog = SecretSantaCog.__new__(SecretSantaCog)
    cog.service = EventService(_MemoryDB())
    cog.dm_ok = True
    cog.dms = []

    async def run(func, *args, **kwargs):
        return func(*args, **kwargs)

    async def dm_card(user_id, card, reply_as=None):
        del user_id, reply_as
        cog.dms.append(card.title)
        return cog.dm_ok

    cog.run = run
    cog._dm_card = dm_card
    return cog


class _Ctx:
    def __init__(self, cog, name, *, failed=False, interaction=None, guild=True):
        self.author = SimpleNamespace(id="ada")
        self.command = SimpleNamespace(name=name, cog=cog)
        self.command_failed = failed
        self.interaction = interaction
        self.guild = SimpleNamespace(id=1) if guild else None
        self.sent = []

    async def send(self, content=None, **kwargs):
        self.sent.append((content, kwargs))


async def test_the_first_command_shows_the_changelog_and_the_next_shows_one_hint():
    cog = _cog()
    first = _Ctx(cog, "join")
    await cog.cog_after_invoke(first)
    assert cog.dms == ["What's new"]
    assert first.sent == []

    second = _Ctx(cog, "join")
    await cog.cog_after_invoke(second)
    assert cog.dms == ["What's new", "Fill in your profile"]

    third = _Ctx(cog, "join")
    await cog.cog_after_invoke(third)
    assert cog.dms == ["What's new", "Fill in your profile"]


async def test_tutorial_and_failed_commands_do_not_spend_a_hint():
    cog = _cog()
    tutorial = _Ctx(cog, "tutorial")
    await cog.cog_after_invoke(tutorial)
    assert cog.dms == []
    assert cog.service.db.seen_changelog == {}

    failed = _Ctx(cog, "join", failed=True)
    await cog.cog_after_invoke(failed)
    assert cog.dms == ["What's new"]
    assert cog.service.claim_command_tip("ada", "join") == "profile"


async def test_help_outside_the_cog_can_show_the_changelog_once():
    cog = _cog()
    ctx = _Ctx(cog, "help")
    ctx.command.cog = None
    await cog.on_command_completion(ctx)
    assert cog.dms == ["What's new"]

    owned = _Ctx(cog, "help")
    await cog.on_command_completion(owned)
    assert cog.dms == ["What's new"]


async def test_a_closed_dm_puts_the_popup_in_the_channel_and_slash_stays_ephemeral():
    cog = _cog()
    cog.dm_ok = False
    prefix = _Ctx(cog, "status")
    await cog.cog_after_invoke(prefix)
    assert cog.dms == ["What's new"]
    assert prefix.sent[0][1]["embed"].title == "What's new"

    class Response:
        def __init__(self):
            self.done = False
            self.messages = []

        def is_done(self):
            return self.done

        async def send_message(self, **kwargs):
            self.messages.append(kwargs)
            self.done = True

    class Followup:
        def __init__(self):
            self.messages = []

        async def send(self, **kwargs):
            self.messages.append(kwargs)

    response = Response()
    followup = Followup()
    slash = _Ctx(cog, "join", interaction=SimpleNamespace(response=response, followup=followup))
    await cog.cog_after_invoke(slash)
    assert response.messages[0]["ephemeral"] is True
    assert response.messages[0]["embed"].title == "Fill in your profile"

    again = _Ctx(
        cog,
        "message",
        interaction=SimpleNamespace(response=Response(), followup=followup),
    )
    again.interaction.response.done = True
    await cog.cog_after_invoke(again)
    assert followup.messages[0]["ephemeral"] is True
    assert followup.messages[0]["embed"].title == "Anonymous notes"


async def test_a_discord_error_while_showing_a_popup_is_swallowed():
    cog = _cog()

    async def broken_dm(user_id, card, reply_as=None):
        del user_id, card, reply_as
        raise discord.HTTPException(SimpleNamespace(status=400, reason="bad"), "nope")

    cog._dm_card = broken_dm
    ctx = _Ctx(cog, "create")
    await cog.cog_after_invoke(ctx)
    assert ctx.sent == []


def test_only_http_images_are_taken_from_attachments():
    gif = SimpleNamespace(content_type="image/gif", filename="note.gif", url="https://cdn.example/note.gif")
    text = SimpleNamespace(content_type="text/plain", filename="notes.txt", url="https://cdn.example/notes.txt")
    local = SimpleNamespace(content_type="image/png", filename="x.png", url="file://x.png")
    ctx = SimpleNamespace(message=SimpleNamespace(attachments=[text, local, gif]))
    assert _attached_image(ctx) == "https://cdn.example/note.gif"
    assert _attached_image(SimpleNamespace(message=SimpleNamespace(attachments=[text]))) == ""
    assert _attached_image(SimpleNamespace(message=None)) == ""


async def test_long_replies_split_and_the_button_is_on_the_last_part():
    sent = []

    class Destination:
        async def send(self, content=None, **kwargs):
            sent.append((content, kwargs))

    await _send_chunks(Destination(), "x" * 1901, reply_as="giftee")
    assert len(sent) == 2
    assert len(sent[0][0]) == 1900
    assert "view" not in sent[0][1]
    assert sent[1][1]["view"].children[0].custom_id == "ss:reply:giftee"
    sent.clear()
    await _send_chunks(Destination(), "")
    assert sent[0][0] == "Done."


async def test_deleting_a_channel_command_ignores_a_closed_permission():
    deleted = []

    class Message:
        async def delete(self):
            deleted.append("tried")
            raise discord.Forbidden(SimpleNamespace(status=403, reason="missing"), "no")

    ctx = SimpleNamespace(guild=SimpleNamespace(id=1), message=Message())
    await _delete_invocation(ctx)
    assert deleted == ["tried"]

    private = SimpleNamespace(guild=None, message=Message())
    await _delete_invocation(private)
    assert deleted == ["tried"]
