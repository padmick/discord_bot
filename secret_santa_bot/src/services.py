"""Secret Santa rules that do not depend on Discord."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from matching import assign_derangement, reroll_after_removal
from utils import log_event
from present import (
    CHANGELOG_VERSION,
    tip_candidates,
    Card,
    format_help,
    label,
    match_card,
    profile_card,
    roster_card,
    settings_card,
    status_card,
    welcome_card,
)

SIGNUP = "signup"
LOCKED = "locked"
DRAWN = "drawn"
DONE = "done"
GIFT_STATUSES = ("not_bought", "bought", "shipped", "received")
GIVER_NEXT = {"not_bought": "bought", "bought": "shipped"}
SENSITIVE_FIELDS = {"wishlist", "address"}
FIELD_LIMITS = {
    "wishlist": 1000,
    "address": 1000,
    "do_not_gift": 500,
    "notes": 500,
}
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
    """Wishlist and address text typed in a server channel must be removed."""
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
        return success(
            f"**Created {label(cleaned, 'Secret Santa')}.** The organizer is not in the draw until they join.",
            event_id=event["id"],
        )

    def join(self, guild_id: str, user_id: str, name: str) -> Result:
        event = self._open(guild_id)
        if isinstance(event, Result):
            return event
        if event["phase"] != SIGNUP:
            return failure("closed", "Joining is closed for this event.")
        self.db.add_participant(event["id"], str(user_id), name)
        return success(
            f"**{label(name, 'Someone')}** joined **{label(event['name'], 'Secret Santa')}**.",
            welcome=welcome_card(name, event["name"]),
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
        self.db.update_event(event["id"], phase=SIGNUP)
        return success("**Signup is open again.**")

    def set_deadline(self, guild_id: str, actor_id: str, is_admin: bool, day: str) -> Result:
        event = self._managed(guild_id, actor_id, is_admin)
        if isinstance(event, Result):
            return event
        try:
            parsed = datetime.strptime(day.strip(), "%Y-%m-%d").date()
        except (TypeError, ValueError):
            return failure("bad_date", "Use a deadline like 2026-12-20.")
        self.db.update_event(event["id"], deadline=parsed)
        return success(f"**Deadline set to {parsed.isoformat()}.**")

    def start(self, guild_id: str, actor_id: str, is_admin: bool) -> Result:
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
        if missing:
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
                "No valid draw exists with these past-match skips. Remove one and try again.",
            )
        self._save_mapping(event["id"], mapping, previous={})
        self.db.update_event(event["id"], phase=DRAWN)
        profiles = {person["user_id"]: person for person in participants}
        return success(
            "**Secret Santa has begun.** Participants received their matches by DM.",
            notifications=[self._notification(giver, receiver, profiles) for giver, receiver in mapping.items()],
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

    def unexclude(self, guild_id: str, user_id: str, other_id: str, other_name: str = "") -> Result:
        event = self._open(guild_id)
        if isinstance(event, Result):
            return event
        user_id = str(user_id)
        known = {person["user_id"] for person in self.db.list_participants(event["id"])}
        if user_id not in known:
            return failure("not_joined", "Join the Secret Santa first.")
        removed = self.db.remove_exclusion_pair(event["id"], user_id, str(other_id))
        if not removed:
            return failure("missing_exclusion", "They aren't on your past-match list.")
        who = label(other_name, "They")
        return success(f"**{who}** can be paired with you again.")

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
        if event["phase"] == DRAWN and wishlist_changed:
            giver_id = self._giver_for(event["id"], user_id)
            if giver_id:
                payload["notify_gifter_id"] = giver_id
                payload["wishlist"] = stored.get("wishlist") or ""
        return success("**Profile saved.**", **payload)

    def my_info(self, guild_id: str, user_id: str) -> Result:
        event = self._open(guild_id)
        if isinstance(event, Result):
            return event
        participant = self.db.get_participant(event["id"], str(user_id))
        if participant is None:
            return failure("not_joined", "You have not joined this Secret Santa.")
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
        card = match_card(receiver)
        return success(card.text(), card=card, **receiver)

    def set_gift_status(self, guild_id: str, user_id: str, status: str) -> Result:
        event = self._open(guild_id)
        if isinstance(event, Result):
            return event
        if event["phase"] != DRAWN:
            return failure("bad_phase", "Gift tracking starts after the draw.")
        status = (status or "").strip().lower()
        user_id = str(user_id)
        pairings = self.db.list_pairings(event["id"])
        if status in GIVER_NEXT.values():
            mine = next((row for row in pairings if row["giver_id"] == user_id), None)
            if mine is None:
                return failure("forbidden", "Only the giver can mark the gift bought or shipped.")
            expected = next(current for current, nxt in GIVER_NEXT.items() if nxt == status)
            if mine["gift_status"] != expected:
                return failure("bad_transition", "That skips a step. Update the gift one step at a time.")
            self.db.set_gift_status(event["id"], user_id, status)
            return success(f"**Marked the gift {status.replace('_', ' ')}.**")
        if status == "received":
            mine = next((row for row in pairings if row["receiver_id"] == user_id), None)
            if mine is None:
                return failure("forbidden", "Only the recipient can mark the gift received.")
            if mine["gift_status"] != "shipped":
                return failure("bad_transition", "The gift has to be shipped before it can be marked received.")
            self.db.set_gift_status(event["id"], mine["giver_id"], "received")
            return success("**Marked the gift received.**")
        return failure("bad_status", "Use bought, shipped, or received.")

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
        )

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
            self._notification(giver, receiver, profiles)
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

    def _notification(self, giver_id: str, receiver_id: str, profiles: dict[str, dict[str, Any]]) -> dict[str, Any]:
        receiver = profiles[receiver_id]
        return {
            "giver_id": giver_id,
            "receiver_id": receiver_id,
            "receiver_name": receiver["name"],
            "wishlist": receiver.get("wishlist") or "",
            "address": receiver.get("address") or "",
            "do_not_gift": receiver.get("do_not_gift") or "",
            "notes": receiver.get("notes") or "",
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


def match_text(receiver: dict[str, Any]) -> str:
    return match_card(receiver).text()


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
