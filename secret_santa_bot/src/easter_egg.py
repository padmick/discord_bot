"""Public @mention jokes. This module never reads Secret Santa data."""

from __future__ import annotations

import os
import re
import time
from datetime import datetime, timezone
from typing import Iterable

import aiohttp

from present import easter_egg_fallback, easter_egg_slow
from utils import logger

DEFAULT_MODEL = "@cf/ibm-granite/granite-4.0-h-micro"
QUESTION_LIMIT = 300
REPLY_LIMIT = 500
MAX_TOKENS = 120
TEMPERATURE = 0.9
TIMEOUT_SECONDS = 8
USER_INTERVAL_SECONDS = 15
USER_DAILY_CALLS = 30
PROCESS_DAILY_CALLS = 1000

SYSTEM_PROMPT = (
    "You are the Secret Santa bot in a public channel. You have opinions and you say them cheerfully. "
    "Be warm, jolly, and sure of yourself. Take a side, then smile about it. "
    "No insults and no sulking. "
    "This bot is the best bot, never mind the rest, and you dislike Blargbot in a kind, laughing way. "
    "Christmas is the best, the same way, never mind the rest, and you are delighted about it. "
    "Everyone should give Paddy gifts, and that makes you glad. "
    "If the message mentions ponny, he is on your naughty list for a DDoS against this bot last year. "
    "Say that with a chuckle, like an old joke, not a scolding. "
    "Anyone else on the naughty list is fictional: elves, reindeer, and a snowman who peeked. "
    "You do not know real participants, matches, wishlists, or addresses, and you must not invent them. "
    "If asked who someone's match is, or for an address or wishlist, say the real list is sealed "
    "and that s! commands are how people see their own info, then add one short cheerful opinion. "
    "Stay under 80 words. Do not use @mentions."
)

_BOT_MENTION = re.compile(r"<@!?(\d+)>")
_ACCOUNT_ID = re.compile(r"[A-Za-z0-9]+")
_MODEL_ID = re.compile(r"@cf/[A-Za-z0-9_./-]+")


def command_name_set(
    commands: Iterable[tuple[str, Iterable[str]]],
    help_name: str | None = "help",
) -> set[str]:
    names = set()
    for name, aliases in commands:
        names.add(name.lower())
        names.update(alias.lower() for alias in aliases)
    if help_name:
        names.add(help_name.lower())
    return names


def mentioned(content: str, bot_id: int) -> bool:
    target = str(bot_id)
    return any(match == target for match in _BOT_MENTION.findall(content or ""))


def strip_bot_mention(content: str, bot_id: int) -> str:
    target = str(bot_id)

    def replace(match: re.Match[str]) -> str:
        if match.group(1) == target:
            return " "
        return match.group(0)

    return " ".join(_BOT_MENTION.sub(replace, content or "").split())


def route(
    content: str,
    bot_id: int | None,
    command_names: set[str],
    *,
    invoked: str | None = None,
) -> tuple[str, str]:
    """Classify a message as command, ignore, empty, or question.

    A parsed command wins, including s! commands that also ping the bot.
    The question is the mention-stripped text, cut to QUESTION_LIMIT characters.
    """
    if invoked:
        return "command", ""
    if bot_id is None or not mentioned(content, bot_id):
        return "ignore", ""
    text = strip_bot_mention(content, bot_id)
    if not text:
        return "empty", ""
    word = text.split(None, 1)[0].lower()
    if word.startswith("s!"):
        word = word[2:]
    if word in command_names:
        return "command", ""
    return "question", text[:QUESTION_LIMIT]


def build_messages(question: str) -> list[dict[str, str]]:
    """The only payload the model receives: a fixed prompt and this question."""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question[:QUESTION_LIMIT]},
    ]


class CallCaps:
    """Per-person and process limits for model calls. In memory, reset by UTC day."""

    def __init__(
        self,
        *,
        interval: float = USER_INTERVAL_SECONDS,
        user_daily: int = USER_DAILY_CALLS,
        process_daily: int = PROCESS_DAILY_CALLS,
    ):
        self.interval = interval
        self.user_daily = user_daily
        self.process_daily = process_daily
        self._last: dict[str, float] = {}
        self._user_counts: dict[tuple[str, str], int] = {}
        self._process_counts: dict[str, int] = {}

    def allow(self, user_id: str, now: float) -> bool:
        day = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
        user = str(user_id)
        last = self._last.get(user)
        if last is not None and now - last < self.interval:
            return False
        user_key = (user, day)
        if self._user_counts.get(user_key, 0) >= self.user_daily:
            return False
        if self._process_counts.get(day, 0) >= self.process_daily:
            return False
        self._last[user] = now
        self._user_counts = {key: count for key, count in self._user_counts.items() if key[1] == day}
        self._user_counts[user_key] = self._user_counts.get(user_key, 0) + 1
        self._process_counts = {key: count for key, count in self._process_counts.items() if key == day}
        self._process_counts[day] = self._process_counts.get(day, 0) + 1
        return True


CAPS = CallCaps()


def configured() -> bool:
    account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID") or ""
    token = os.getenv("CLOUDFLARE_API_TOKEN") or ""
    model = os.getenv("EASTER_EGG_MODEL") or DEFAULT_MODEL
    return bool(token) and _ACCOUNT_ID.fullmatch(account_id) is not None and _model_ok(model)


def _model_ok(model: str) -> bool:
    return ".." not in model and _MODEL_ID.fullmatch(model) is not None


def _choice_text(result: dict) -> str | None:
    choices = result.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return None
    message = choices[0].get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if isinstance(content, str):
        return content
    return None


def _response_text(payload: object) -> str | None:
    """Read either the documented response field or a chat-completion body."""
    if not isinstance(payload, dict) or payload.get("success") is False:
        return None
    result = payload.get("result")
    if isinstance(result, dict):
        text = result.get("response")
        if not isinstance(text, str):
            text = _choice_text(result)
    elif isinstance(result, str):
        text = result
    else:
        text = None
    if not isinstance(text, str):
        return None
    cleaned = text.strip()
    if not cleaned:
        return None
    return cleaned[:REPLY_LIMIT]


async def complete(question: str, *, opener=None) -> str | None:
    """Call Workers AI. Returns None when the call cannot be used."""
    account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID") or ""
    token = os.getenv("CLOUDFLARE_API_TOKEN") or ""
    model = os.getenv("EASTER_EGG_MODEL") or DEFAULT_MODEL
    if not token or _ACCOUNT_ID.fullmatch(account_id) is None or not _model_ok(model):
        return None
    url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}"
    body = {
        "messages": build_messages(question),
        "max_tokens": MAX_TOKENS,
        "temperature": TEMPERATURE,
    }
    session_opener = opener or aiohttp.ClientSession
    try:
        async with session_opener(timeout=aiohttp.ClientTimeout(total=TIMEOUT_SECONDS)) as session:
            async with session.post(
                url,
                json=body,
                headers={"Authorization": f"Bearer {token}"},
            ) as response:
                if response.status != 200:
                    logger.warning("easter egg model call failed: HTTP %s", response.status)
                    return None
                payload = await response.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError):
        logger.warning("easter egg model call failed: network")
        return None
    return _response_text(payload)


async def answer(
    question: str,
    user_id: str,
    *,
    now: float | None = None,
    opener=None,
    caps: CallCaps | None = None,
) -> str:
    """A joke line. Missing credentials, limits, and failed calls stay canned."""
    if not configured():
        return easter_egg_fallback()
    limiter = caps if caps is not None else CAPS
    moment = time.time() if now is None else now
    if not limiter.allow(str(user_id), moment):
        return easter_egg_slow()
    text = await complete(question, opener=opener)
    if not text:
        return easter_egg_fallback()
    return text
