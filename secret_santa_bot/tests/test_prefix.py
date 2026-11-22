"""The s! mark can have a space after it, in either case."""

from types import SimpleNamespace

import discord
from discord.ext import commands

from bot import bot


def _message(content: str):
    return SimpleNamespace(content=content, author=SimpleNamespace(id=9), _state=None)


async def test_a_space_after_the_prefix_still_finds_the_command():
    assert bot.strip_after_prefix is True
    probe = commands.Bot(
        command_prefix=bot.command_prefix,
        case_insensitive=True,
        strip_after_prefix=bot.strip_after_prefix,
        intents=discord.Intents.default(),
    )
    probe._connection.user = SimpleNamespace(id=1)

    @probe.command(name="tutorial")
    async def tutorial(ctx, page: str = ""):
        del ctx, page

    spaced = await probe.get_context(_message("S! tutorial"))
    assert spaced.prefix == "S!"
    assert spaced.invoked_with == "tutorial"
    assert spaced.command is tutorial
    assert spaced.view.read_rest() == ""

    capital = await probe.get_context(_message("S!  Tutorial extra"))
    assert capital.command is tutorial
    assert capital.invoked_with == "Tutorial"
    assert capital.view.read_rest().strip() == "extra"

    tight = await probe.get_context(_message("s!tutorial"))
    assert tight.command is tutorial

    plain = await probe.get_context(_message("tutorial"))
    assert plain.command is None
    assert plain.prefix is None
