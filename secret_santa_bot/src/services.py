"""Secret Santa rules that do not depend on Discord."""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from matching import assign_derangement, is_derangement, reroll_after_removal, reroll_for_exclusions
from utils import log_event
from present import (
    CHANGELOG_VERSION,
    tip_candidates,
    Card,
    format_help,
    label,
    blocks_card,
    anytime_hint_card,
    event_setup_card,
    hints_index_card,
    match_card,
    open_announcement_card,
    resolve_hint,
    setup_card,
    notes_card,
    profile_card,
    reports_card,
    roster_card,
    settings_card,
    status_card,
    waiting_card,
    welcome_card,
)

SIGNUP = "signup"
LOCKED = "locked"
DRAWN = "drawn"
DONE = "done"
GIFT_STATUSES = ("not_bought", "bought", "shipped", "received")
GIVER_NEXT = {"not_bought": "bought", "bought": "shipped"}
SENSITIVE_FIELDS = {"wishlist", "address", "budget", "delivery_window"}
FIELD_LIMITS = {
    "wishlist": 1000,
    "address": 1000,
    "do_not_gift": 500,
    "notes": 500,
    "budget": 80,
    "delivery_window": 300,
}
_TRACKING_LINK = re.compile(r"https?://[^\s<>]{1,500}\Z", re.IGNORECASE)
DEFAULT_REMINDER_INTERVAL_DAYS = 7
DEFAULT_SHIPPING_LEAD_DAYS = 3
MAX_REMINDER_DAYS = 30


@dataclass
class Result:
    ok: bool
    code: str
    message: str
    data: dict[str, Any] | None = None
    card: Card | None = None


def success(message: str, *, card: Card | None = None, **data: Any) -> Result:
    return Result(True, "ok", message, data or None, card)


def failure(code: str, message: str, **data: Any) -> Result:
    return Result(False, code, message, data or None)


def missing_fields(participant: dict[str, Any]) -> list[str]:
    missing = []
    if not (participant.get("wishlist") or "").strip():
        missing.append("wishlist")
    if not participant.get("digital_only") and not (participant.get("address") or "").strip():
        missing.append("address")
    return missing


def should_delete_channel_text(field_name: str, in_guild: bool) -> bool:
    """Private profile text typed in a server channel must be removed."""
    return bool(in_guild) and field_name in SENSITIVE_FIELDS


def anonymous_message_log(success_flag: bool, direction: str, display_name: str = "") -> str:
    """Log an anonymous message without the sender's name."""
    del display_name
    status = "ok" if success_flag else "failed"
    return f"{status} anonymous {direction}"


def resolve_dm_events(participations: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the only open participation, or None when the DM is ambiguous."""
    if len(participations) == 1:
        return participations[0]
    return None


class EventService:
    def __init__(self, db, rng: random.Random | None = None):
        self.db = db
        self.rng = rng or random.Random()

    def create_event(self, guild_id: str, organizer_id: str, name: str = "Secret Santa") -> Result:
        guild_id = str(guild_id)
        organizer_id = str(organizer_id)
        cleaned = (name or "").strip() or "Secret Santa"
        if len(cleaned) > 100:
            return failure("bad_name", "The event name must be 100 characters or fewer.")
        if self.db.get_open_event(guild_id):
            return failure(
                "event_exists",
                "This server already has a Secret Santa event. Finish or cancel it before creating another.",
            )
        event = self.db.create_event(guild_id, cleaned, organizer_id)
        message = f"**Created {label(cleaned, 'Secret Santa')}.** The organizer is not in the draw until they join."
        locks = self._finished_locks(guild_id)
        lock_notice = ""
        if locks:
            message += "\nLast year's locked pairs are in your DMs."
            lock_notice = (
                f"Last time these pairs were locked: {_lock_phrase(locks)}. "
                "`s!carrylocks` keeps them. `s!unblock` drops one after that."
            )
        return success(
            message,
            event_id=event["id"],
            event=event,
            carried_locks=locks,
            lock_notice=lock_notice,
        )

    def join(self, guild_id: str, user_id: str, name: str) -> Result:
        self.close_expired_signups(date.today())
        event = self._open(guild_id)
        if isinstance(event, Result):
            return event
        if event["phase"] != SIGNUP:
            return failure("closed", "Joining is closed for this event.")
        person = label(name, "Someone")
        event_name = label(event["name"], "Secret Santa")
        existing = self.db.get_participant(event["id"], str(user_id))
        self.db.add_participant(event["id"], str(user_id), name)
        if existing:
            return success(f"**{person}** is already in **{event_name}**.")
        partners = self.db.list_past_partners(str(guild_id), str(user_id))
        return success(
            f"**{person}** joined **{event_name}**.",
            welcome=welcome_card(name, event["name"], event.get("rules") or "", partners),
            past_partners=partners,
        )

    def leave(self, guild_id: str, user_id: str) -> Result:
        return self._drop(guild_id, str(user_id), acting_on_self=True)

    def remove(self, guild_id: str, actor_id: str, target_id: str, is_admin: bool) -> Result:
        event = self._open(guild_id)
        if isinstance(event, Result):
            return event
        denied = self._manager(event, actor_id, is_admin)
        if denied:
            return denied
        return self._drop(guild_id, str(target_id), acting_on_self=False)

    def lock(self, guild_id: str, actor_id: str, is_admin: bool) -> Result:
        event = self._managed(guild_id, actor_id, is_admin)
        if isinstance(event, Result):
            return event
        if event["phase"] != SIGNUP:
            return failure("bad_phase", "Signup can only be locked before the draw.")
        self.db.update_event(event["id"], phase=LOCKED)
        return success("**Signup is locked.** Profile edits are still open.")

    def unlock(self, guild_id: str, actor_id: str, is_admin: bool) -> Result:
        event = self._managed(guild_id, actor_id, is_admin)
        if isinstance(event, Result):
            return event
        if event["phase"] != LOCKED:
            return failure("bad_phase", "Only a locked signup can be unlocked.")
        signup_by = event.get("signup_deadline")
        if signup_by and date.today() > signup_by:
            return failure(
                "signup_closed",
                "That signup date has passed. Set a later day with s!signupby, or clear it.",
            )
        self.db.update_event(event["id"], phase=SIGNUP)
        return success("**Signup is open again.**")

    def set_deadline(self, guild_id: str, actor_id: str, is_admin: bool, day: str) -> Result:
        event = self._managed(guild_id, actor_id, is_admin)
        if isinstance(event, Result):
            return event
        parsed = _parse_day(day)
        if parsed is None:
            return failure("bad_date", "Use a deadline like 2026-12-20.")
        self.db.update_event(event["id"], deadline=parsed)
        return success(f"**Gift deadline set to {parsed.isoformat()}.**")

    def set_signup_deadline(self, guild_id: str, actor_id: str, is_admin: bool, day: str) -> Result:
        event = self._managed(guild_id, actor_id, is_admin)
        if isinstance(event, Result):
            return event
        text = (day or "").strip()
        if not text:
            current = event.get("signup_deadline")
            shown = current.isoformat() if current else "not set"
            gift = event["deadline"].isoformat() if event.get("deadline") else "not set"
            return success(f"**Last day to join is {shown}.** The gift deadline is {gift}.")
        if text.lower() == "clear":
            self.db.update_event(event["id"], signup_deadline=None)
            extra = " Signup stays closed until you run s!unlock." if event["phase"] == LOCKED else ""
            return success("**The signup date is cleared.**" + extra)
        parsed = _parse_day(text)
        if parsed is None:
            return failure("bad_date", "Use a date like 2026-12-01.")
        self.db.update_event(event["id"], signup_deadline=parsed)
        if event["phase"] == SIGNUP and date.today() > parsed:
            self.db.update_event(event["id"], phase=LOCKED)
            return success(f"**Last day to join was {parsed.isoformat()}.** Signup is now closed.")
        return success(
            f"**Last day to join is {parsed.isoformat()}.** "
            "People can join through that day. The gift deadline is separate."
        )

    def setup_overview(self, guild_id: str, actor_id: str, is_admin: bool) -> Result:
        event = self._managed(guild_id, actor_id, is_admin)
        if isinstance(event, Result):
            return event
        card = setup_card(event)
        return success(card.text(), card=card, event=event)

    def apply_setup(
        self,
        guild_id: str,
        actor_id: str,
        is_admin: bool,
        signup: str = "",
        deadline: str = "",
        cap: str = "",
        rules: str = "",
    ) -> Result:
        event = self._managed(guild_id, actor_id, is_admin)
        if isinstance(event, Result):
            return event
        signup_text = (signup or "").strip()
        deadline_text = (deadline or "").strip()
        cap_text = (cap or "").strip()
        rules_text = (rules or "").strip()
        unchanged = success(
            "Nothing changed. Fill in a line to set it, or type clear to remove it.",
            card=setup_card(event),
        )
        if not any((signup_text, deadline_text, cap_text, rules_text)):
            return unchanged
        fields: dict[str, Any] = {}
        parsed_signup = None
        if signup_text:
            if signup_text.lower() == "clear":
                if event.get("signup_deadline") is not None:
                    fields["signup_deadline"] = None
            else:
                parsed_signup = _parse_day(signup_text)
                if parsed_signup is None:
                    return failure("bad_date", "Use a signup date like 2026-12-01, or clear.")
                if _current_day(event.get("signup_deadline")) != parsed_signup:
                    fields["signup_deadline"] = parsed_signup
                else:
                    parsed_signup = None
        if deadline_text:
            if deadline_text.lower() == "clear":
                if event.get("deadline") is not None:
                    fields["deadline"] = None
            else:
                parsed_deadline = _parse_day(deadline_text)
                if parsed_deadline is None:
                    return failure("bad_date", "Use a gift deadline like 2026-12-20, or clear.")
                if _current_day(event.get("deadline")) != parsed_deadline:
                    fields["deadline"] = parsed_deadline
        if cap_text:
            if cap_text.lower() == "clear":
                if event.get("spending_cap") is not None:
                    fields["spending_cap"] = None
            else:
                cleaned_cap = " ".join(cap_text.split())
                if len(cleaned_cap) > 80:
                    return failure("too_long", "Keep the spending cap under 80 characters.")
                if _current_copy(event.get("spending_cap")) != cleaned_cap:
                    fields["spending_cap"] = cleaned_cap
        if rules_text:
            if rules_text.lower() == "clear":
                if event.get("rules") is not None:
                    fields["rules"] = None
            else:
                cleaned_rules = " ".join(rules_text.split())
                if len(cleaned_rules) > 500:
                    return failure("too_long", "Keep the rules note under 500 characters.")
                if _current_copy(event.get("rules")) != cleaned_rules:
                    fields["rules"] = cleaned_rules
        if not fields:
            return unchanged
        closed_now = (
            parsed_signup is not None and event["phase"] == SIGNUP and date.today() > parsed_signup
        )
        if closed_now:
            fields["phase"] = LOCKED
        self.db.update_event(event["id"], **fields)
        updated = self.db.get_event(event["id"]) or event
        extra = " Signup is now closed because that date has passed." if closed_now else ""
        return success("**Setup saved.**" + extra, card=setup_card(updated))

    def prepare_announcement(self, guild_id: str, actor_id: str, is_admin: bool, channel_id: str) -> Result:
        event = self._managed(guild_id, actor_id, is_admin)
        if isinstance(event, Result):
            return event
        if event["phase"] != SIGNUP:
            return failure("bad_phase", "The open announcement is for signup. Unlock signup before posting it.")
        channel = str(channel_id or "").strip()
        if not channel.isdigit():
            return failure("bad_channel", "Choose a text channel in this server.")
        card = open_announcement_card(event)
        return success(card.text(), card=card, channel_id=channel)

    def remember_announcement(self, guild_id: str, actor_id: str, is_admin: bool, channel_id: str) -> Result:
        event = self._managed(guild_id, actor_id, is_admin)
        if isinstance(event, Result):
            return event
        channel = str(channel_id or "").strip()
        if not channel.isdigit():
            return failure("bad_channel", "Choose a text channel in this server.")
        self.db.update_event(event["id"], announce_channel_id=channel)
        return success("Saved.", channel_id=channel)

    def show_hint(self, topic: str = "") -> Result:
        if not str(topic or "").strip():
            card = hints_index_card()
            return success(card.text(), card=card)
        found = resolve_hint(topic)
        if not found:
            return failure("unknown_hint", "Try s!hint with no topic to see the list.")
        card = anytime_hint_card(found)
        return success(card.text(), card=card)

    def close_expired_signups(self, today: date) -> int:
        closed = 0
        for event in self.db.list_actionable_events():
            signup_by = event.get("signup_deadline")
            if event["phase"] == SIGNUP and signup_by and today > signup_by:
                self.db.update_event(event["id"], phase=LOCKED)
                closed += 1
        return closed

    def carry_locks(self, guild_id: str, actor_id: str, is_admin: bool) -> Result:
        event = self._managed(guild_id, actor_id, is_admin)
        if isinstance(event, Result):
            return event
        if event["phase"] == DONE:
            return failure("bad_phase", "This event is already finished.")
        locks = self._finished_locks(str(guild_id))
        if not locks:
            return success("**The last finished event had no organizer locks.**")
        pair_ids = [(left, right) for left, right, _name_left, _name_right in locks]
        applied = self._apply_locked_pairs(event, pair_ids)
        if isinstance(applied, Result):
            return applied
        changed, redrawn = applied
        names = _lock_phrase(locks)
        message = f"**Carried {names}.** They cannot remove these. `s!unblock` drops one."
        if event["phase"] == DRAWN and redrawn:
            message += " Changed matches were sent by DM."
        elif event["phase"] == DRAWN:
            message += " Names are already out, so this applies if the matches are drawn again."
        return success(message, changed=changed)

    def unshipped(self, guild_id: str, actor_id: str, is_admin: bool) -> Result:
        event = self._managed(guild_id, actor_id, is_admin)
        if isinstance(event, Result):
            return event
        if event["phase"] != DRAWN:
            return failure("bad_phase", "Gift progress is available after the draw.")
        profiles = {person["user_id"]: person for person in self.db.list_participants(event["id"])}
        waiting = []
        for pairing in self.db.list_pairings(event["id"]):
            if pairing["gift_status"] in {"shipped", "received"}:
                continue
            giver = profiles.get(pairing["giver_id"], {})
            waiting.append(
                {
                    "user_id": pairing["giver_id"],
                    "name": giver.get("name") or "Someone",
                    "gift_status": pairing["gift_status"],
                }
            )
        card = waiting_card(waiting)
        return success(card.text(), card=card, waiting=waiting)

    def start(self, guild_id: str, actor_id: str, is_admin: bool, allow_missing_wishlist: bool = False) -> Result:
        event = self._managed(guild_id, actor_id, is_admin)
        if isinstance(event, Result):
            return event
        if event["phase"] not in (SIGNUP, LOCKED):
            return failure("bad_phase", "This event has already been drawn.")
        participants = self.db.list_participants(event["id"])
        if len(participants) < 2:
            return failure("too_small", "At least two participants are required.")
        missing = [
            {"user_id": person["user_id"], "name": person["name"], "missing": missing_fields(person)}
            for person in participants
            if missing_fields(person)
        ]
        blocking = missing
        if allow_missing_wishlist:
            blocking = [
                {**person, "missing": ["address"]}
                for person in missing
                if "address" in person["missing"]
            ]
        if blocking:
            if allow_missing_wishlist:
                return failure(
                    "missing_info",
                    "Cannot start. Some participants still need an address. "
                    "A missing wishlist does not block this draw.",
                    missing=blocking,
                )
            return failure(
                "missing_info",
                "Cannot start. Some participants still need a wishlist or address.",
                missing=missing,
            )
        user_ids = [person["user_id"] for person in participants]
        mapping = assign_derangement(user_ids, self.db.list_exclusions(event["id"]), self.rng)
        if mapping is None:
            return failure(
                "no_draw",
                "No valid draw exists with these skips and organizer locks. Remove one and try again.",
            )
        self._save_mapping(event["id"], mapping, previous={})
        self.db.update_event(event["id"], phase=DRAWN)
        profiles = {person["user_id"]: person for person in participants}
        wishlist_missing = [person for person in missing if "wishlist" in person["missing"]]
        message = "**Secret Santa has begun.** Participants received their matches by DM."
        extra: dict[str, Any] = {}
        if wishlist_missing:
            names = ", ".join(label(person["name"], "Someone") for person in wishlist_missing)
            message = (
                "**Secret Santa has begun** without every wishlist. "
                "Participants received their matches by DM. "
                f"Still missing a wishlist: {names}."
            )
            notice_ids: list[str] = []
            for user_id in [event["organizer_id"], *(person["user_id"] for person in participants)]:
                user_id = str(user_id)
                if user_id not in notice_ids:
                    notice_ids.append(user_id)
            extra = {"missing_wishlist": wishlist_missing, "notice_user_ids": notice_ids}
        return success(
            message,
            notifications=[
                self._notification(giver, receiver, profiles, event) for giver, receiver in mapping.items()
            ],
            **extra,
        )

    def finish(self, guild_id: str, actor_id: str, is_admin: bool) -> Result:
        event = self._managed(guild_id, actor_id, is_admin)
        if isinstance(event, Result):
            return event
        if event["phase"] != DRAWN:
            return failure("bad_phase", "Finish is available after the draw.")
        self.db.update_event(event["id"], phase=DONE)
        return success("**The event is finished.** You can create a new one in this server.")

    def cancel(self, guild_id: str, actor_id: str, is_admin: bool) -> Result:
        event = self._managed(guild_id, actor_id, is_admin)
        if isinstance(event, Result):
            return event
        self.db.delete_event(event["id"])
        return success("**The Secret Santa event was cancelled.**")

    def status(self, guild_id: str) -> Result:
        event = self._open(guild_id)
        if isinstance(event, Result):
            return event
        participants = self.db.list_participants(event["id"])
        missing_wishlist = [person["name"] for person in participants if "wishlist" in missing_fields(person)]
        missing_address = [person["name"] for person in participants if "address" in missing_fields(person)]
        gift_counts = None
        if event["phase"] == DRAWN:
            gift_counts = {status: 0 for status in GIFT_STATUSES}
            for pairing in self.db.list_pairings(event["id"]):
                gift_counts[pairing["gift_status"]] += 1
        payload = {
            "name": event["name"],
            "phase": event["phase"],
            "deadline": event["deadline"].isoformat() if event["deadline"] else None,
            "signup_deadline": event["signup_deadline"].isoformat() if event.get("signup_deadline") else None,
            "count": len(participants),
            "missing_wishlist": missing_wishlist,
            "missing_address": missing_address,
            "gift_counts": gift_counts,
        }
        card = status_card(payload)
        return success(card.text(), card=card, **payload)

    def roster(self, guild_id: str) -> Result:
        event = self._open(guild_id)
        if isinstance(event, Result):
            return event
        participants = self.db.list_participants(event["id"])
        card = roster_card(event["name"], [person["name"] for person in participants])
        return success(card.text(), card=card, participants=participants)

    def exclude(self, guild_id: str, user_id: str, other_id: str, other_name: str = "") -> Result:
        return self.skip_past_matches(guild_id, user_id, [(other_id, other_name)])

    def skip_past_matches(self, guild_id: str, user_id: str, others: list[tuple[str, str]]) -> Result:
        """Remember people this participant was paired with before.

        The other person does not have to have joined. The draw keeps the two
        of them from being assigned to each other if both are in it.
        """
        event = self._open(guild_id)
        if isinstance(event, Result):
            return event
        if event["phase"] == DONE:
            return failure("bad_phase", "This event is already finished.")
        user_id = str(user_id)
        known = {person["user_id"] for person in self.db.list_participants(event["id"])}
        if user_id not in known:
            return failure(
                "not_joined",
                "Join the Secret Santa first, then choose anyone you were paired with before.",
            )
        saved: list[str] = []
        seen: set[str] = set()
        for other_id, other_name in others:
            other_id = str(other_id).strip()
            if not other_id or other_id == user_id or other_id in seen:
                continue
            seen.add(other_id)
            self.db.add_exclusion_pair(event["id"], user_id, other_id)
            saved.append(label(other_name, "them"))
        if not saved:
            return failure("bad_exclusion", "Pick someone else. You can't skip yourself.")
        message = (
            f"You won't be paired with {_people_phrase(saved)} this time. "
            "They don't have to have joined yet."
        )
        if event["phase"] == DRAWN:
            message += " Names are already out, so this applies if the matches are drawn again."
        return success(message, skipped=saved)

    def unexclude(
        self,
        guild_id: str,
        user_id: str,
        other_id: str,
        other_name: str = "",
        actor_name: str = "",
    ) -> Result:
        event = self._open(guild_id)
        if isinstance(event, Result):
            return event
        user_id = str(user_id)
        known = {person["user_id"] for person in self.db.list_participants(event["id"])}
        if user_id not in known:
            return failure("not_joined", "Join the Secret Santa first.")
        removed = self.db.remove_exclusion_pair(event["id"], user_id, str(other_id))
        if removed == "locked":
            who = label(actor_name, "Someone")
            other = label(other_name, "Someone")
            return failure(
                "locked",
                "The organizer kept this pair apart. Ask them to remove the lock.",
                organizer_id=str(event["organizer_id"]),
                organizer_notice=f"**{who}** tried to remove the lock with **{other}**. The lock stays.",
            )
        if removed != "removed":
            return failure("missing_exclusion", "They aren't on your past-match list.")
        who = label(other_name, "They")
        return success(f"**{who}** can be paired with you again.")

    def block_pair(
        self,
        guild_id: str,
        actor_id: str,
        is_admin: bool,
        first_id: str,
        first_name: str,
        second_id: str,
        second_name: str,
    ) -> Result:
        event = self._managed(guild_id, actor_id, is_admin)
        if isinstance(event, Result):
            return event
        if event["phase"] == DONE:
            return failure("bad_phase", "This event is already finished.")
        first_id, second_id = str(first_id).strip(), str(second_id).strip()
        if not first_id or not second_id or first_id == second_id:
            return failure("bad_exclusion", "Pick two different people.")
        names = f"{label(first_name, 'Someone')} and {label(second_name, 'Someone')}"
        proposed = set(self.db.list_exclusions(event["id"]))
        proposed.add((first_id, second_id))
        proposed.add((second_id, first_id))
        changed: list[dict[str, Any]] = []
        if event["phase"] == DRAWN:
            current_rows = {row["giver_id"]: row for row in self.db.list_pairings(event["id"])}
            mapping = {giver: row["receiver_id"] for giver, row in current_rows.items()}
            if mapping and not is_derangement(mapping, list(mapping), proposed):
                redrawn = reroll_for_exclusions(mapping, proposed, self.rng)
                if redrawn is None:
                    return failure(
                        "no_draw",
                        f"{names} cannot be separated without breaking the draw, so nothing was locked.",
                    )
                self.db.add_exclusion_pair(event["id"], first_id, second_id, locked=True)
                self._save_mapping(event["id"], redrawn, previous=current_rows)
                profiles = {person["user_id"]: person for person in self.db.list_participants(event["id"])}
                changed = [
                    self._notification(giver, receiver, profiles, event)
                    for giver, receiver in redrawn.items()
                    if current_rows.get(giver, {}).get("receiver_id") != receiver
                ]
                return success(
                    f"**{names}** will not be matched. They cannot remove this. Changed matches were sent by DM.",
                    changed=changed,
                )
        self.db.add_exclusion_pair(event["id"], first_id, second_id, locked=True)
        message = f"**{names}** will not be matched. They cannot remove this."
        if event["phase"] == DRAWN:
            message += " Names are already out, so this applies if the matches are drawn again."
        else:
            message += " They do not have to have joined yet."
        return success(message, changed=changed)

    def unblock_pair(
        self,
        guild_id: str,
        actor_id: str,
        is_admin: bool,
        first_id: str,
        second_id: str,
        first_name: str = "",
        second_name: str = "",
    ) -> Result:
        event = self._managed(guild_id, actor_id, is_admin)
        if isinstance(event, Result):
            return event
        removed = self.db.remove_exclusion_pair(event["id"], str(first_id), str(second_id), include_locked=True)
        if removed != "removed":
            return failure("missing_exclusion", "Those two are not locked apart.")
        names = f"{label(first_name, 'Someone')} and {label(second_name, 'Someone')}"
        return success(
            f"**{names}** can be matched again. A personal skip has to be added again if they still want one."
        )

    def locked_blocks(self, guild_id: str, actor_id: str, is_admin: bool) -> Result:
        event = self._managed(guild_id, actor_id, is_admin)
        if isinstance(event, Result):
            return event
        pairs = self.db.list_locked_pairs(event["id"])
        if not pairs:
            return success("**No organizer locks yet.**")
        card = blocks_card(pairs)
        return success(card.text(), card=card, pairs=pairs)

    def set_event_copy(self, guild_id: str, actor_id: str, is_admin: bool, field: str, text: str) -> Result:
        event = self._managed(guild_id, actor_id, is_admin)
        if isinstance(event, Result):
            return event
        if field not in {"spending_cap", "rules"}:
            return failure("bad_field", "That event note is not editable.")
        if not str(text or "").strip():
            card = event_setup_card(event.get("spending_cap") or "", event.get("rules") or "")
            return success(card.text(), card=card)
        cleaned = " ".join(str(text).split())
        if cleaned.lower() == "clear":
            cleaned = ""
        limit = 80 if field == "spending_cap" else 500
        if len(cleaned) > limit:
            label_name = "spending cap" if field == "spending_cap" else "rules note"
            return failure("too_long", f"Keep the {label_name} under {limit} characters.")
        self.db.update_event(event["id"], **{field: cleaned or None})
        updated = self.db.get_event(event["id"]) or event
        card = event_setup_card(updated.get("spending_cap") or "", updated.get("rules") or "")
        return success("**Event setup saved.**\n" + card.text(), card=card)

    def set_text_field(
        self,
        guild_id: str,
        user_id: str,
        field_name: str,
        value: str,
        in_guild: bool,
    ) -> Result:
        delete_message = should_delete_channel_text(field_name, in_guild)
        if field_name not in FIELD_LIMITS:
            return failure("bad_field", "That profile field is not editable.", delete_message=delete_message)
        cleaned = (value or "").strip()
        if field_name == "delivery_window" and cleaned.lower() == "clear":
            cleaned = ""
        limit = FIELD_LIMITS[field_name]
        if len(cleaned) > limit:
            return failure(
                "too_long",
                f"Keep {field_name} under {limit} characters.",
                delete_message=delete_message,
            )
        return self.update_profile(
            guild_id,
            user_id,
            delete_message=delete_message,
            **{field_name: cleaned or None},
        )

    def update_profile(self, guild_id: str, user_id: str, delete_message: bool = False, **fields: Any) -> Result:
        event = self._open(guild_id)
        if isinstance(event, Result):
            return event
        if event["phase"] not in (SIGNUP, LOCKED, DRAWN):
            return failure("bad_phase", "Profiles are closed for this event.", delete_message=delete_message)
        unknown = set(fields) - (FIELD_LIMITS.keys() | {"digital_only"})
        if unknown:
            return failure("bad_field", "That profile field is not editable.", delete_message=delete_message)
        for field_name, value in fields.items():
            if field_name == "digital_only":
                continue
            if value is not None and len(str(value)) > FIELD_LIMITS[field_name]:
                return failure(
                    "too_long",
                    f"Keep {field_name} under {FIELD_LIMITS[field_name]} characters.",
                    delete_message=delete_message,
                )
        user_id = str(user_id)
        current = self.db.get_participant(event["id"], user_id)
        if current is None:
            return failure("not_joined", "You have not joined this Secret Santa.", delete_message=delete_message)
        stored = dict(fields)
        for field_name, value in list(stored.items()):
            if field_name != "digital_only" and isinstance(value, str):
                stored[field_name] = value.strip() or None
        rowcount = self.db.update_profile(event["id"], user_id, **stored)
        if rowcount == 0:
            return failure("not_joined", "You have not joined this Secret Santa.", delete_message=delete_message)
        payload: dict[str, Any] = {}
        if delete_message:
            payload["delete_message"] = True
        wishlist_changed = "wishlist" in stored and (stored.get("wishlist") or "") != (current.get("wishlist") or "")
        window_changed = (
            "delivery_window" in stored
            and (stored.get("delivery_window") or "") != (current.get("delivery_window") or "")
        )
        if event["phase"] == DRAWN and (wishlist_changed or window_changed):
            giver_id = self._giver_for(event["id"], user_id)
            if giver_id:
                payload["notify_gifter_id"] = giver_id
                if wishlist_changed:
                    payload["wishlist"] = stored.get("wishlist") or ""
                if window_changed:
                    payload["delivery_window"] = stored.get("delivery_window") or ""
        return success("**Profile saved.**", **payload)

    def my_info(self, guild_id: str, user_id: str) -> Result:
        event = self._open(guild_id)
        if isinstance(event, Result):
            return event
        participant = self.db.get_participant(event["id"], str(user_id))
        if participant is None:
            return failure("not_joined", "You have not joined this Secret Santa.")
        participant = dict(participant)
        participant["spending_cap"] = event.get("spending_cap") or ""
        participant["rules"] = event.get("rules") or ""
        card = profile_card(participant)
        return success(card.text(), card=card, **participant)

    def match_info(self, guild_id: str, user_id: str) -> Result:
        event = self._open(guild_id)
        if isinstance(event, Result):
            return event
        if event["phase"] not in (DRAWN, DONE):
            return failure("bad_phase", "Matches are sent when the draw starts.")
        pairing = self._pairing_for_giver(event["id"], str(user_id))
        if pairing is None:
            return failure("no_match", "You do not have a match in this event.")
        receiver = self.db.get_participant(event["id"], pairing["receiver_id"])
        if receiver is None:
            return failure("no_match", "The match could not be loaded.")
        receiver = dict(receiver)
        receiver["spending_cap"] = event.get("spending_cap") or ""
        receiver["rules"] = event.get("rules") or ""
        card = match_card(receiver)
        return success(card.text(), card=card, **receiver)

    def set_gift_status(self, guild_id: str, user_id: str, status: str, tracking: str = "") -> Result:
        event = self._open(guild_id)
        if isinstance(event, Result):
            return event
        if event["phase"] != DRAWN:
            return failure("bad_phase", "Gift tracking starts after the draw.")
        status = (status or "").strip().lower()
        user_id = str(user_id)
        link = tracking_link(tracking)
        if link is None:
            return failure("bad_link", "Use a tracking link that starts with http:// or https://.")
        if link and status not in {"shipped", "tracking"}:
            return failure("bad_link", "A tracking link is saved when the gift is marked shipped.")
        pairings = self.db.list_pairings(event["id"])
        if status == "tracking":
            if not link:
                return failure("bad_link", "Add the tracking link after the word tracking.")
            mine = next((row for row in pairings if row["giver_id"] == user_id), None)
            if mine is None:
                return failure("forbidden", "Only the giver can add a tracking link.")
            if mine["gift_status"] not in {"shipped", "received"}:
                return failure("bad_transition", "Mark the gift shipped before adding a tracking link.")
            self.db.set_gift_status(event["id"], user_id, mine["gift_status"], tracking_url=link)
            return success(
                "**Tracking link saved.** It was sent to the person receiving the gift.",
                notify_receiver_id=mine["receiver_id"],
                tracking_url=link,
            )
        if status in GIVER_NEXT.values():
            mine = next((row for row in pairings if row["giver_id"] == user_id), None)
            if mine is None:
                return failure("forbidden", "Only the giver can mark the gift bought or shipped.")
            expected = next(current for current, nxt in GIVER_NEXT.items() if nxt == status)
            if mine["gift_status"] != expected:
                return failure("bad_transition", "That skips a step. Update the gift one step at a time.")
            saved_link = link or None
            self.db.set_gift_status(event["id"], user_id, status, tracking_url=saved_link)
            if status == "shipped":
                message = "**Marked the gift shipped.**"
                if link:
                    message += " The tracking link was sent to them."
                return success(
                    message,
                    notify_receiver_id=mine["receiver_id"],
                    tracking_url=link,
                )
            return success(f"**Marked the gift {status.replace('_', ' ')}.**")
        if status == "received":
            mine = next((row for row in pairings if row["receiver_id"] == user_id), None)
            if mine is None:
                return failure("forbidden", "Only the recipient can mark the gift received.")
            if mine["gift_status"] != "shipped":
                return failure("bad_transition", "The gift has to be shipped before it can be marked received.")
            self.db.set_gift_status(event["id"], mine["giver_id"], "received")
            return success("**Marked the gift received.**")
        return failure("bad_status", "Use bought, shipped, received, or tracking.")

    def prepare_message(
        self,
        guild_id: str,
        user_id: str,
        recipient_type: str,
        text: str,
        image: str = "",
    ) -> Result:
        event = self._open(guild_id)
        if isinstance(event, Result):
            return event
        if event["phase"] != DRAWN:
            return failure("bad_phase", "Anonymous messages are available after the draw.")
        direction = (recipient_type or "").strip().lower()
        if direction not in ("gifter", "giftee"):
            return failure("bad_target", "Use gifter or giftee.")
        body = (text or "").strip()
        picture = image if str(image).startswith(("http://", "https://")) else ""
        if not body and not picture:
            return failure("empty", "Write a message, emoji, or GIF link first.")
        if len(body) > 1000:
            return failure("too_long", "Keep the message under 1000 characters.")
        user_id = str(user_id)
        if direction == "gifter":
            target_id = self._giver_for(event["id"], user_id)
            reply_as = "giftee"
        else:
            pairing = self._pairing_for_giver(event["id"], user_id)
            target_id = pairing["receiver_id"] if pairing else None
            reply_as = "gifter"
        if not target_id:
            return failure("no_match", "You do not have a match for that message yet.")
        return success(
            "Message ready.",
            target_id=target_id,
            reply_as=reply_as,
            body=body,
            direction=direction,
            image=picture,
            event_id=event["id"],
        )

    def remember_note(
        self,
        event_id: int,
        sender_id: str,
        recipient_id: str,
        body: str,
        image: str = "",
    ) -> None:
        self.db.save_note(int(event_id), str(sender_id), str(recipient_id), body or "", image or "")

    def my_notes(self, guild_id: str, user_id: str) -> Result:
        event = self._open(guild_id)
        if isinstance(event, Result):
            return event
        if event["phase"] != DRAWN:
            return failure("bad_phase", "Anonymous notes are available after the draw.")
        user_id = str(user_id)
        pairing = self._pairing_for_giver(event["id"], user_id)
        giftee_id = pairing["receiver_id"] if pairing else ""
        santa_id = self._giver_for(event["id"], user_id) or ""
        rows = self.db.list_notes(event["id"], user_id)
        if not rows:
            return success("**No anonymous notes yet.**")

        def rank(row: dict[str, Any]) -> int:
            sender, recipient = row["sender_id"], row["recipient_id"]
            if sender == user_id and recipient == giftee_id:
                return 0
            if recipient == user_id and sender == giftee_id:
                return 1
            if sender == user_id and recipient == santa_id:
                return 2
            if recipient == user_id and sender == santa_id:
                return 3
            return 4

        entries = []
        for row in sorted(rows, key=rank):
            title = _note_title(user_id, row["sender_id"], row["recipient_id"], giftee_id, santa_id)
            entries.append((title, row.get("body") or "", row.get("image_url") or ""))
        card = notes_card(entries)
        return success(card.text(), card=card)

    def submit_feedback(self, guild_id: str, user_id: str, kind: str, text: str) -> Result:
        body = " ".join((text or "").split())
        if kind not in {"problem", "feature"}:
            return failure("bad_kind", "Choose a problem or a feature request.")
        if not body:
            return failure("empty", "Write a short note first.")
        if len(body) > 1000:
            return failure("too_long", "Keep the note to 1000 characters.")
        self.db.add_feedback(str(guild_id or ""), str(user_id), kind, body)
        label_kind = "PROBLEM" if kind == "problem" else "FEATURE"
        log_event(label_kind, f"server {guild_id or 'dm'} user {user_id}: {body}")
        if kind == "problem":
            return success("Problem report saved. Thank you for telling us.")
        return success("Feature request saved. Thank you for the idea.")

    def feedback_report(self, guild_id: str, actor_id: str, is_admin: bool) -> Result:
        event = self.db.get_latest_event(str(guild_id))
        if event is None:
            return failure("no_event", "There is no Secret Santa event in this server yet.")
        denied = self._manager(event, actor_id, is_admin)
        if denied:
            return denied
        rows = self.db.list_feedback(str(guild_id), limit=10)
        if not rows:
            return success("**No problem reports or feature requests for this server yet.**")
        card = reports_card(rows)
        return success(card.text(), card=card)

    def claim_changelog(self, user_id: str) -> bool:
        return bool(self.db.claim_changelog(str(user_id), CHANGELOG_VERSION))

    def claim_tip(self, user_id: str, tip_id: str) -> bool:
        if not tip_id:
            return False
        return bool(self.db.claim_tip(str(user_id), tip_id))

    def claim_command_tip(self, user_id: str, command_name: str) -> str:
        for tip_id in tip_candidates(command_name):
            if self.claim_tip(user_id, tip_id):
                return tip_id
        return ""

    def reveal(self, guild_id: str, actor_id: str, is_admin: bool, confirmation: str) -> Result:
        event = self._managed(guild_id, actor_id, is_admin)
        if isinstance(event, Result):
            return event
        if event["phase"] != DRAWN:
            return failure("bad_phase", "There is no draw to reveal yet.")
        if (confirmation or "").strip().lower() != "reveal":
            return failure(
                "confirm",
                "This shows every match and breaks anonymity. Confirm with the word reveal.",
            )
        profiles = {person["user_id"]: person for person in self.db.list_participants(event["id"])}
        rows = []
        for pairing in self.db.list_pairings(event["id"]):
            giver = profiles.get(pairing["giver_id"], {})
            receiver = profiles.get(pairing["receiver_id"], {})
            rows.append(
                {
                    "giver_id": pairing["giver_id"],
                    "giver_name": giver.get("name", pairing["giver_id"]),
                    "receiver_id": pairing["receiver_id"],
                    "receiver_name": receiver.get("name", pairing["receiver_id"]),
                }
            )
        lines = ["Full draw. Anonymity is broken for the organizer.", ""]
        lines.extend(f"{row['giver_name']} -> {row['receiver_name']}" for row in rows)
        return success(
            "**Sent in a DM.** Anonymity is broken for you. The pairs were not posted here.",
            pairings=rows,
            dm_text="\n".join(lines),
            event_id=event["id"],
        )

    def missing_profile_targets(self, guild_id: str, actor_id: str, is_admin: bool) -> Result:
        event = self._managed(guild_id, actor_id, is_admin)
        if isinstance(event, Result):
            return event
        users = []
        for person in self.db.list_participants(event["id"]):
            missing = missing_fields(person)
            if missing:
                users.append({"user_id": person["user_id"], "name": person["name"], "missing": missing})
        if not users:
            return success("**Everyone is ready.** The draw has the profiles it needs.", users=[])
        return success(f"**{len(users)} participant(s)** still need profile details.", users=users)

    def broadcast_recipients(self, guild_id: str, actor_id: str, is_admin: bool) -> Result:
        event = self._managed(guild_id, actor_id, is_admin)
        if isinstance(event, Result):
            return event
        participants = self.db.list_participants(event["id"])
        if not participants:
            return failure("empty", "Nobody has joined yet.")
        return success("Broadcast ready.", user_ids=[person["user_id"] for person in participants])

    def open_event(self, guild_id: str) -> dict[str, Any] | None:
        return self.db.get_open_event(str(guild_id))

    def open_participations(self, user_id: str) -> list[dict[str, Any]]:
        return self.db.list_open_participations(str(user_id))

    def notification_settings(self, guild_id: str, actor_id: str, is_admin: bool) -> Result:
        event = self._managed(guild_id, actor_id, is_admin)
        if isinstance(event, Result):
            return event
        payload = _settings_payload(event)
        card = settings_card(
            payload["profile_reminders"],
            payload["shipping_reminders"],
            payload["reminder_interval_days"],
            payload["shipping_lead_days"],
        )
        return success(card.text(), card=card, **payload)

    def update_notification_settings(
        self,
        guild_id: str,
        actor_id: str,
        is_admin: bool,
        profile_reminders: str,
        shipping_reminders: str,
        reminder_interval_days: str,
        shipping_lead_days: str,
    ) -> Result:
        event = self._managed(guild_id, actor_id, is_admin)
        if isinstance(event, Result):
            return event
        profile_on = parse_toggle(profile_reminders)
        shipping_on = parse_toggle(shipping_reminders)
        if profile_on is None or shipping_on is None:
            return failure("bad_setting", "Use on or off for each reminder.")
        try:
            interval = int(str(reminder_interval_days).strip())
            lead = int(str(shipping_lead_days).strip())
        except (TypeError, ValueError):
            return failure("bad_setting", "Reminder timing must be a whole number of days.")
        if not 1 <= interval <= MAX_REMINDER_DAYS:
            return failure("bad_setting", f"Days between reminders must be from 1 to {MAX_REMINDER_DAYS}.")
        if not 0 <= lead <= MAX_REMINDER_DAYS:
            return failure(
                "bad_setting",
                f"Shipping reminders can start from 0 to {MAX_REMINDER_DAYS} days before the deadline.",
            )
        self.db.update_event(
            event["id"],
            profile_reminders=profile_on,
            shipping_reminders=shipping_on,
            reminder_interval_days=interval,
            shipping_lead_days=lead,
        )
        updated = self.db.get_event(event["id"])
        payload = _settings_payload(updated)
        card = settings_card(
            payload["profile_reminders"],
            payload["shipping_reminders"],
            payload["reminder_interval_days"],
            payload["shipping_lead_days"],
        )
        return success("Notification settings saved.\n" + card.text(), card=card, **payload)

    def select_reminders(self, today: date) -> list[dict[str, Any]]:
        selected = []
        for event in self.db.list_actionable_events():
            interval = int(event.get("reminder_interval_days") or DEFAULT_REMINDER_INTERVAL_DAYS)
            profile_due = (
                event.get("profile_reminders", True)
                and event["phase"] in (SIGNUP, LOCKED)
                and reminder_interval_elapsed(event.get("last_profile_reminder"), today, interval)
            )
            if profile_due:
                for person in self.db.list_participants(event["id"]):
                    missing = missing_fields(person)
                    if missing:
                        selected.append(
                            {
                                "kind": "profile",
                                "event_id": event["id"],
                                "guild_id": event["guild_id"],
                                "user_id": person["user_id"],
                                "missing": missing,
                            }
                        )
            deadline = event["deadline"]
            lead = int(event.get("shipping_lead_days") if event.get("shipping_lead_days") is not None else DEFAULT_SHIPPING_LEAD_DAYS)
            due = (
                event.get("shipping_reminders", True)
                and event["phase"] == DRAWN
                and deadline is not None
                and shipping_reminder_due(deadline, today, lead)
                and reminder_interval_elapsed(event.get("last_shipping_reminder"), today, interval)
            )
            if due:
                for pairing in self.db.list_pairings(event["id"]):
                    if pairing["gift_status"] not in ("shipped", "received"):
                        selected.append(
                            {
                                "kind": "shipping",
                                "event_id": event["id"],
                                "guild_id": event["guild_id"],
                                "user_id": pairing["giver_id"],
                                "deadline": deadline,
                            }
                        )
        return selected

    def mark_reminded(self, event_id: int, kind: str, today: date) -> None:
        if kind == "profile":
            self.db.update_event(event_id, last_profile_reminder=today)
        elif kind == "shipping":
            self.db.update_event(event_id, last_shipping_reminder=today)
        else:
            raise ValueError(f"Unknown reminder kind: {kind}")

    def _finished_locks(self, guild_id: str) -> list[tuple[str, str, str, str]]:
        finished = self.db.latest_finished_event(str(guild_id))
        if not finished:
            return []
        names = {person["user_id"]: person.get("name") or "" for person in self.db.list_participants(finished["id"])}
        return [
            (left, right, names.get(left, ""), names.get(right, ""))
            for left, right in self.db.list_locked_pairs(finished["id"])
            if left and right and left != right
        ]

    def _apply_locked_pairs(self, event: dict[str, Any], pair_ids: list[tuple[str, str]]) -> Result | tuple:
        proposed = set(self.db.list_exclusions(event["id"]))
        for left, right in pair_ids:
            proposed.add((str(left), str(right)))
            proposed.add((str(right), str(left)))
        changed: list[dict[str, Any]] = []
        redrawn = False
        if event["phase"] == DRAWN:
            current_rows = {row["giver_id"]: row for row in self.db.list_pairings(event["id"])}
            mapping = {giver: row["receiver_id"] for giver, row in current_rows.items()}
            if mapping and not is_derangement(mapping, list(mapping), proposed):
                updated = reroll_for_exclusions(mapping, proposed, self.rng)
                if updated is None:
                    return failure(
                        "no_draw",
                        "Those locks cannot be applied without breaking the draw, so nothing changed.",
                    )
                for left, right in pair_ids:
                    self.db.add_exclusion_pair(event["id"], left, right, locked=True)
                self._save_mapping(event["id"], updated, previous=current_rows)
                profiles = {person["user_id"]: person for person in self.db.list_participants(event["id"])}
                changed = [
                    self._notification(giver, receiver, profiles, event)
                    for giver, receiver in updated.items()
                    if current_rows.get(giver, {}).get("receiver_id") != receiver
                ]
                return changed, True
        for left, right in pair_ids:
            self.db.add_exclusion_pair(event["id"], left, right, locked=True)
        return changed, redrawn

    def _drop(self, guild_id: str, user_id: str, acting_on_self: bool) -> Result:
        event = self._open(guild_id)
        if isinstance(event, Result):
            return event
        participant = self.db.get_participant(event["id"], user_id)
        if participant is None:
            return failure("not_joined", "That person is not in this event.")
        if event["phase"] == LOCKED:
            return failure("closed", "The roster is locked. Unlock signup before changing it.")
        if event["phase"] == SIGNUP:
            self.db.delete_participant(event["id"], user_id)
            verb = "You left" if acting_on_self else f"{label(participant['name'], 'They')} was removed from"
            return success(f"**{verb} the event.**")
        if event["phase"] != DRAWN:
            return failure("bad_phase", "The roster cannot change in this phase.")

        current = {row["giver_id"]: row for row in self.db.list_pairings(event["id"])}
        mapping = {giver: row["receiver_id"] for giver, row in current.items()}
        redrawn = reroll_after_removal(mapping, user_id, self.db.list_exclusions(event["id"]), self.rng)
        if redrawn is None:
            return failure(
                "no_draw",
                "The remaining participants cannot be redrawn, so nobody was removed.",
            )
        self._save_mapping(event["id"], redrawn, previous=current)
        self.db.delete_participant(event["id"], user_id)
        profiles = {person["user_id"]: person for person in self.db.list_participants(event["id"])}
        changed = [
            self._notification(giver, receiver, profiles, event)
            for giver, receiver in redrawn.items()
            if current.get(giver, {}).get("receiver_id") != receiver
        ]
        verb = "You left" if acting_on_self else f"{label(participant['name'], 'They')} was removed from"
        return success(f"**{verb} the event.** Changed matches were sent by DM.", changed=changed)

    def _save_mapping(self, event_id: int, mapping: dict[str, str], previous: dict[str, dict[str, str]]) -> None:
        pairs = []
        for giver, receiver in mapping.items():
            prior = previous.get(giver)
            status = prior["gift_status"] if prior and prior["receiver_id"] == receiver else "not_bought"
            pairs.append((giver, receiver, status))
        self.db.replace_pairings(event_id, pairs)

    def _notification(
        self,
        giver_id: str,
        receiver_id: str,
        profiles: dict[str, dict[str, Any]],
        event: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        receiver = profiles[receiver_id]
        event = event or {}
        return {
            "giver_id": giver_id,
            "receiver_id": receiver_id,
            "receiver_name": receiver["name"],
            "wishlist": receiver.get("wishlist") or "",
            "address": receiver.get("address") or "",
            "do_not_gift": receiver.get("do_not_gift") or "",
            "notes": receiver.get("notes") or "",
            "budget": receiver.get("budget") or "",
            "delivery_window": receiver.get("delivery_window") or "",
            "spending_cap": event.get("spending_cap") or "",
            "rules": event.get("rules") or "",
            "digital_only": bool(receiver.get("digital_only")),
        }

    def _giver_for(self, event_id: int, receiver_id: str) -> str | None:
        for pairing in self.db.list_pairings(event_id):
            if pairing["receiver_id"] == receiver_id:
                return pairing["giver_id"]
        return None

    def _pairing_for_giver(self, event_id: int, giver_id: str) -> dict[str, str] | None:
        for pairing in self.db.list_pairings(event_id):
            if pairing["giver_id"] == giver_id:
                return pairing
        return None

    def _open(self, guild_id: str) -> dict[str, Any] | Result:
        event = self.db.get_open_event(str(guild_id))
        if event is None:
            return failure("no_event", "There is no active Secret Santa event.")
        return event

    def _managed(self, guild_id: str, actor_id: str, is_admin: bool) -> dict[str, Any] | Result:
        event = self._open(guild_id)
        if isinstance(event, Result):
            return event
        denied = self._manager(event, actor_id, is_admin)
        if denied:
            return denied
        return event

    @staticmethod
    def _manager(event: dict[str, Any], actor_id: str, is_admin: bool) -> Result | None:
        if is_admin or str(actor_id) == str(event["organizer_id"]):
            return None
        return failure("forbidden", "Only the organizer or a server admin can do that.")


def _people_phrase(names: list[str]) -> str:
    bolded = [f"**{name}**" for name in names]
    if len(bolded) == 1:
        return bolded[0]
    if len(bolded) == 2:
        return f"{bolded[0]} and {bolded[1]}"
    return ", ".join(bolded[:-1]) + f", and {bolded[-1]}"


def tracking_link(value: str) -> str | None:
    """Return a cleaned http(s) link, an empty string when none was given, or None when it is unusable."""
    text = " ".join((value or "").split())
    if not text:
        return ""
    if _TRACKING_LINK.fullmatch(text):
        return text
    return None


def _note_title(user_id: str, sender_id: str, recipient_id: str, giftee_id: str, santa_id: str) -> str:
    if sender_id == user_id and recipient_id == giftee_id:
        return "To the person you're buying for"
    if recipient_id == user_id and sender_id == giftee_id:
        return "From the person you're buying for"
    if sender_id == user_id and recipient_id == santa_id:
        return "To the person buying your gift"
    if recipient_id == user_id and sender_id == santa_id:
        return "From the person buying your gift"
    if sender_id == user_id:
        return "A note you sent earlier"
    return "A note you received earlier"


def match_text(receiver: dict[str, Any]) -> str:
    return match_card(receiver).text()


def _parse_day(day: str) -> date | None:
    try:
        return datetime.strptime(day.strip(), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _current_day(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return _parse_day(str(value))


def _current_copy(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())


def _lock_phrase(locks: list[tuple[str, str, str, str]]) -> str:
    return "; ".join(
        f"{label(left_name, 'Someone')} and {label(right_name, 'Someone')}"
        for _left, _right, left_name, right_name in locks
    )


def shipping_reminder_due(deadline: date, today: date, lead_days: int) -> bool:
    return 0 <= (deadline - today).days <= lead_days


def reminder_interval_elapsed(last: date | None, today: date, interval_days: int) -> bool:
    if last is None:
        return True
    return (today - last).days >= interval_days


def parse_toggle(value: str) -> bool | None:
    cleaned = str(value or "").strip().lower()
    if cleaned in {"on", "yes", "true", "1"}:
        return True
    if cleaned in {"off", "no", "false", "0"}:
        return False
    return None


def _settings_payload(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "profile_reminders": bool(event.get("profile_reminders", True)),
        "shipping_reminders": bool(event.get("shipping_reminders", True)),
        "reminder_interval_days": int(event.get("reminder_interval_days") or DEFAULT_REMINDER_INTERVAL_DAYS),
        "shipping_lead_days": int(
            event.get("shipping_lead_days")
            if event.get("shipping_lead_days") is not None
            else DEFAULT_SHIPPING_LEAD_DAYS
        ),
    }


def assignment_text(note: dict[str, Any]) -> str:
    receiver = {
        "name": note["receiver_name"],
        "wishlist": note.get("wishlist"),
        "address": note.get("address"),
        "do_not_gift": note.get("do_not_gift"),
        "notes": note.get("notes"),
        "budget": note.get("budget"),
        "delivery_window": note.get("delivery_window"),
        "spending_cap": note.get("spending_cap"),
        "rules": note.get("rules"),
        "digital_only": note.get("digital_only"),
    }
    return match_text(receiver)


# Imported by the rate limiter tests through services to keep the rule next
# to the other event rules. The cog uses the same class.
@dataclass
class RateLimiter:
    max_commands: int = 5
    window: int = 30
    enabled: bool = True
    history: dict[str, list[float]] = field(default_factory=dict)

    def check(self, user_id: str, now: float) -> tuple[bool, int]:
        """Return (limited, seconds_remaining). Allowed calls are recorded."""
        if not self.enabled:
            return False, 0
        recent = [stamp for stamp in self.history.get(user_id, []) if now - stamp < self.window]
        if len(recent) >= self.max_commands:
            self.history[user_id] = recent
            remaining = int(self.window - (now - min(recent)))
            return True, max(1, remaining)
        recent.append(now)
        self.history[user_id] = recent
        return False, 0
