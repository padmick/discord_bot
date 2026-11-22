"""@mention jokes stay off the command path and away from Secret Santa data."""

import ast
from pathlib import Path
from types import SimpleNamespace

import aiohttp
import discord
import pytest

import bot as bot_module
import easter_egg
from easter_egg import (
    DEFAULT_MODEL,
    PROCESS_DAILY_CALLS,
    SYSTEM_PROMPT,
    USER_DAILY_CALLS,
    USER_INTERVAL_SECONDS,
    CallCaps,
    answer,
    build_messages,
    command_name_set,
    route,
)
from present import (
    EASTER_EGG_FALLBACK,
    EASTER_EGG_SLOW,
    EASTER_EGG_YOU_RANG,
    safe,
)


NAMES = command_name_set([("join", ("signup",)), ("help", ())])


def test_a_real_command_is_not_an_egg():
    kind, question = route("<@42> join", 42, NAMES)
    assert kind == "command"
    assert question == ""

    kind, _ = route("<@!42> Help", 42, NAMES)
    assert kind == "command"

    kind, _ = route("<@42> s!join now", 42, NAMES)
    assert kind == "command"

    kind, _ = route("s!join <@42>", 42, NAMES, invoked="join")
    assert kind == "command"

    kind, _ = route("hello there", 42, NAMES)
    assert kind == "ignore"


def test_a_naughty_list_question_is_an_egg():
    kind, question = route("<@42> who's on your naughty list?", 42, NAMES)
    assert kind == "question"
    assert question == "who's on your naughty list?"

    kind, question = route("<@42>", 42, NAMES)
    assert kind == "empty"
    assert question == ""


def test_the_prompt_has_opinions_and_keeps_the_list_sealed():
    assert "You have opinions and you say them." in SYSTEM_PROMPT
    assert "you dislike Blargbot" in SYSTEM_PROMPT
    assert "the best bot, never mind the rest" in SYSTEM_PROMPT
    assert "Christmas is the best" in SYSTEM_PROMPT
    assert "Everyone should give Paddy gifts." in SYSTEM_PROMPT
    assert "mentions ponny" in SYSTEM_PROMPT
    assert "DDoS against this bot last year" in SYSTEM_PROMPT
    assert "must not invent them" in SYSTEM_PROMPT
    assert "Do not use @mentions." in SYSTEM_PROMPT


def test_the_prompt_contains_only_the_question():
    question = "who's on your naughty list?"
    messages = build_messages(question)
    assert messages == [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]
    assert list(messages[1]) == ["role", "content"]
    assert "database" not in messages[1]["content"]
    long_question = "snow " * 200
    assert len(build_messages(long_question)[1]["content"]) == 300


def test_the_egg_module_does_not_import_secret_santa_data():
    tree = ast.parse(Path(easter_egg.__file__).read_text(encoding="utf-8"))
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert not any(name.split(".")[0] in {"database", "services"} for name in imported)


class _Response:
    def __init__(self, status, payload):
        self.status = status
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def json(self, content_type=None):
        del content_type
        return self.payload


class _Session:
    def __init__(self, status, payload, sink):
        self.status = status
        self.payload = payload
        self.sink = sink

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def post(self, url, json, headers):
        self.sink.append({"url": url, "json": json, "headers": headers})
        return _Response(self.status, self.payload)


def _opener(status, payload, sink, *, error=None):
    def open_session(*_args, **_kwargs):
        if error is not None:
            raise error
        return _Session(status, payload, sink)

    return open_session


@pytest.mark.asyncio
async def test_http_errors_fall_back(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "abc123")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "token")
    monkeypatch.delenv("EASTER_EGG_MODEL", raising=False)
    sink = []
    caps = CallCaps(interval=0, user_daily=5, process_daily=5)
    text = await answer(
        "who's on your naughty list?",
        "9",
        now=0,
        caps=caps,
        opener=_opener(410, {"success": False}, sink),
    )
    assert text in EASTER_EGG_FALLBACK
    assert sink[0]["json"]["messages"] == build_messages("who's on your naughty list?")
    assert sink[0]["json"]["max_tokens"] == 120
    assert sink[0]["json"]["temperature"] == 0.9
    assert sink[0]["url"].endswith("/ai/run/" + DEFAULT_MODEL)
    assert sink[0]["headers"]["Authorization"] == "Bearer token"

    text = await answer(
        "who's on your naughty list?",
        "9",
        now=1,
        caps=caps,
        opener=_opener(200, {}, sink, error=aiohttp.ClientError("down")),
    )
    assert text in EASTER_EGG_FALLBACK

    chat = {
        "success": True,
        "result": {"choices": [{"message": {"content": "  The snowman peeked.  "}}]},
    }
    text = await answer(
        "who's on your naughty list?",
        "9",
        now=2,
        caps=caps,
        opener=_opener(200, chat, sink),
    )
    assert text == "The snowman peeked."


@pytest.mark.asyncio
async def test_missing_credentials_skip_the_network(monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)

    def opener(*_args, **_kwargs):
        raise AssertionError("the network should stay unused")

    text = await answer("who's on your naughty list?", "9", opener=opener)
    assert text in EASTER_EGG_FALLBACK


def test_caps_stop_further_calls():
    assert USER_INTERVAL_SECONDS == 15
    assert USER_DAILY_CALLS == 30
    assert PROCESS_DAILY_CALLS == 1000

    caps = CallCaps(interval=15, user_daily=2, process_daily=10)
    assert caps.allow("9", 0)
    assert not caps.allow("9", 14)
    assert caps.allow("9", 15)
    assert not caps.allow("9", 30)

    shared = CallCaps(interval=0, user_daily=10, process_daily=1)
    assert shared.allow("1", 0)
    assert not shared.allow("2", 0)


@pytest.mark.asyncio
async def test_a_second_quick_mention_does_not_call_the_model(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "abc123")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "token")
    sink = []
    caps = CallCaps(interval=15, user_daily=5, process_daily=5)
    opener = _opener(200, {"success": True, "result": {"response": "The snowman peeked."}}, sink)
    first = await answer("who's naughty?", "9", now=0, caps=caps, opener=opener)
    second = await answer("and the reindeer?", "9", now=5, caps=caps, opener=opener)
    assert first == "The snowman peeked."
    assert second in EASTER_EGG_SLOW
    assert len(sink) == 1


def _message(content, *, author_id=9, bot=False):
    message = SimpleNamespace(content=content, author=SimpleNamespace(id=author_id, bot=bot))

    async def reply(text, **kwargs):
        message.sent = (text, kwargs)

    message.reply = reply
    message.sent = None
    return message


@pytest.mark.asyncio
async def test_on_message_keeps_join_on_the_command_path(monkeypatch):
    seen = []

    async def process_commands(message):
        seen.append(message.content)

    async def get_context(_message):
        return SimpleNamespace(command=SimpleNamespace(name="join"))

    monkeypatch.setattr(bot_module.bot, "process_commands", process_commands)
    monkeypatch.setattr(bot_module.bot, "get_context", get_context)
    message = _message("<@1> join")
    await bot_module.on_message(message)
    assert seen == ["<@1> join"]
    assert message.sent is None


@pytest.mark.asyncio
async def test_on_message_replies_to_a_naughty_list_question(monkeypatch):
    async def process_commands(_message):
        raise AssertionError("a joke should not run the command parser")

    async def get_context(_message):
        return SimpleNamespace(command=None)

    async def fake_answer(question, user_id):
        assert question == "who's on your naughty list?"
        assert user_id == "9"
        return "The snowman is on it @everyone"

    monkeypatch.setattr(bot_module.bot, "process_commands", process_commands)
    monkeypatch.setattr(bot_module.bot, "get_context", get_context)
    monkeypatch.setattr(bot_module.bot._connection, "user", SimpleNamespace(id=1))
    monkeypatch.setattr(bot_module, "answer", fake_answer)
    message = _message("<@1> who's on your naughty list?")
    await bot_module.on_message(message)
    text, kwargs = message.sent
    assert text == safe("The snowman is on it @everyone")
    assert "@everyone" not in text
    mentions = kwargs["allowed_mentions"]
    assert mentions.everyone is False
    assert mentions.users is False
    assert mentions.roles is False
    assert isinstance(mentions, discord.AllowedMentions)


@pytest.mark.asyncio
async def test_on_message_uses_a_canned_line_for_a_bare_mention(monkeypatch):
    async def get_context(_message):
        return SimpleNamespace(command=None)

    monkeypatch.setattr(bot_module.bot, "get_context", get_context)
    monkeypatch.setattr(bot_module.bot._connection, "user", SimpleNamespace(id=1))
    message = _message("<@1>")
    await bot_module.on_message(message)
    text, _kwargs = message.sent
    assert text in EASTER_EGG_YOU_RANG


@pytest.mark.asyncio
async def test_on_message_ignores_other_bots(monkeypatch):
    async def get_context(_message):
        raise AssertionError("bot messages are ignored")

    monkeypatch.setattr(bot_module.bot, "get_context", get_context)
    message = _message("<@1> who's on your naughty list?", bot=True)
    await bot_module.on_message(message)
    assert message.sent is None
