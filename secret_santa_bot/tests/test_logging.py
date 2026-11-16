"""Failures must be readable from stderr, which is what App Platform stores."""

from types import SimpleNamespace

from discord.ext import commands

from bot import on_app_command_error, on_command_error, record_command_problem
from cogs.secret_santa import RateLimited, SecretSantaCog
from utils import report_failure, root_exception
from views import ProfileModal, ProfileView, ReplyView


def _raised(message: str) -> RuntimeError:
    try:
        raise RuntimeError(message)
    except RuntimeError as exc:
        return exc


class _AppFailure(Exception):
    def __init__(self, original: BaseException):
        super().__init__(f"Command 'profile' raised an exception: {type(original).__name__}: {original}")
        self.original = original


class _HybridFailure(Exception):
    def __init__(self, original: BaseException):
        super().__init__(f"Hybrid command raised an error: {original}")
        self.original = original


def _context(command: str = "profile", guild_id=None, user_id: int = 8):
    sent: list[str] = []

    async def send(message: str):
        sent.append(message)

    ctx = SimpleNamespace(
        command=SimpleNamespace(qualified_name=command),
        guild=None if guild_id is None else SimpleNamespace(id=guild_id),
        author=SimpleNamespace(id=user_id),
        sent=sent,
        send=send,
    )
    return ctx


def test_wrapped_command_failure_logs_the_root_traceback(capsys):
    database_error = _raised('column reference "id" is ambiguous')
    wrapped = _HybridFailure(_AppFailure(database_error))
    assert root_exception(wrapped) is database_error

    record_command_problem("command profile guild dm user 8", wrapped)
    err = capsys.readouterr().err
    assert "ERROR secret_santa command profile guild dm user 8 failed: RuntimeError:" in err
    assert 'column reference "id" is ambiguous' in err
    assert "Traceback (most recent call last)" in err
    assert "Hybrid command raised an error" not in err.split("Traceback", 1)[0]


def test_usage_mistakes_are_info_and_rate_limits_are_silent(capsys):
    record_command_problem("command unknown guild dm user 3", RateLimited())
    assert capsys.readouterr().err == ""

    record_command_problem("command unknown guild dm user 3", commands.CommandNotFound())
    err = capsys.readouterr().err
    assert "INFO secret_santa command unknown guild dm user 3 rejected: CommandNotFound" in err
    assert "ERROR" not in err


async def test_prefix_profile_exception_is_logged_and_shown(capsys):
    database_error = _raised('column reference "id" is ambiguous')
    ctx = _context()
    await on_command_error(ctx, _HybridFailure(_AppFailure(database_error)))
    err = capsys.readouterr().err
    assert "ERROR secret_santa command profile guild dm user 8 failed: RuntimeError:" in err
    assert "Traceback (most recent call last)" in err
    assert ctx.sent[0].startswith("An error occurred: Command 'profile' raised an exception:")
    assert "ambiguous" in ctx.sent[0]


async def test_slash_profile_exception_is_logged(capsys):
    sent: list[str] = []

    class Response:
        def is_done(self):
            return False

        async def send_message(self, message, ephemeral=False):
            del ephemeral
            sent.append(message)

    interaction = SimpleNamespace(
        command=SimpleNamespace(qualified_name="profile"),
        guild_id=42,
        user=SimpleNamespace(id=8),
        response=Response(),
    )
    database_error = _raised('column reference "id" is ambiguous')
    await on_app_command_error(interaction, _AppFailure(database_error))
    err = capsys.readouterr().err
    assert "ERROR secret_santa command profile guild 42 user 8 failed: RuntimeError:" in err
    assert "ambiguous" in sent[0]


async def test_profile_form_failure_is_logged_for_the_user(capsys):
    class Cog:
        service = SimpleNamespace(update_profile=object())

        async def run(self, *args, **kwargs):
            del args, kwargs
            raise _raised('column reference "id" is ambiguous')

    class Response:
        async def defer(self, ephemeral=False):
            del ephemeral

    class Followup:
        def __init__(self):
            self.messages: list[str] = []

        async def send(self, message, ephemeral=False):
            del ephemeral
            self.messages.append(message)

    interaction = SimpleNamespace(user=SimpleNamespace(id=4), response=Response(), followup=Followup())
    modal = ProfileModal(Cog(), "guild-1", "4", {})
    await modal.on_submit(interaction)
    err = capsys.readouterr().err
    assert "ERROR secret_santa profile form guild guild-1 user 4 failed: RuntimeError:" in err
    assert "ambiguous" in err
    assert "Traceback (most recent call last)" in err
    assert interaction.followup.messages == ["The profile could not be saved."]


async def test_view_and_reminder_failures_are_logged(capsys):
    class Response:
        def is_done(self):
            return True

    class Followup:
        def __init__(self):
            self.messages: list[str] = []

        async def send(self, message, ephemeral=False):
            del ephemeral
            self.messages.append(message)

    interaction = SimpleNamespace(response=Response(), followup=Followup())
    view = ProfileView(None, "guild-1", "4", False)
    await view.on_error(interaction, _raised("button failed"), None)
    reply = ReplyView("gifter")
    await reply.on_error(interaction, _raised("reply button failed"), None)
    await SecretSantaCog._on_reminder_error(None, _raised("reminder failed"))
    err = capsys.readouterr().err
    assert "ProfileView guild guild-1 user 4 failed: RuntimeError: button failed" in err
    assert "ReplyView failed: RuntimeError: reply button failed" in err
    assert "daily reminders failed: RuntimeError: reminder failed" in err
    assert err.count("Traceback (most recent call last)") == 3
    assert interaction.followup.messages == ["That action failed.", "That action failed."]
