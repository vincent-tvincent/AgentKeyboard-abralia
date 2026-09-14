# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Notification presentation state, separate from call and question ownership.

Only the broker's worker mutates this state. Records are immutable and maps are
replaced on each update so the service's shallow recovery checkpoints remain
valid. Renderer snapshots never contain allocation ownership tokens.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from copy import deepcopy
import secrets


NOTIFICATION_ONSET_SECONDS = .58
_PENDING = frozenset(('queued', 'active', 'muted'))
_VISIBLE = frozenset(('waiting', 'presenting', 'orb'))


@dataclass(frozen=True)
class Orb:
    key: str
    formed_at: float | None = None
    ends_at: float | None = None
    dismissed_at: float | None = None
    dismiss_opacity: float = 1.0
    quiet: bool = False


@dataclass(frozen=True)
class Presentation:
    token: str
    notice_ids: tuple[str, ...]
    started_at: float
    paused_at: float | None = None
    animation: dict | None = None


def _smooth(value: float) -> float:
    value = min(1.0, max(0.0, value))
    return value * value * (3.0 - 2.0 * value)


class NotificationVisualState:
    def __init__(self):
        self.notices: dict[tuple[str, str], str] = {}
        self.orbs: dict[str, Orb] = {}
        self.presentation: Presentation | None = None

    @staticmethod
    def _duration(config):
        return NOTIFICATION_ONSET_SECONDS + config.notification_breath_seconds + config.orb_formation_seconds

    @staticmethod
    def _opacity(orb, now, config):
        if orb.dismissed_at is not None:
            return orb.dismiss_opacity * (1 - _smooth((now - orb.dismissed_at) / config.orb_dismiss_seconds))
        if orb.formed_at is None or orb.ends_at is None:
            return 1.0
        fade_start = max(orb.formed_at, orb.ends_at - config.orb_fade_seconds)
        return 1 - _smooth((now - fade_start) / max(orb.ends_at - fade_start, 1e-9))

    def acknowledge(self, token, notice_ids):
        """Acknowledge the exact notice IDs captured at the user-action boundary."""
        changes = {(token, notice_id): 'seen' for notice_id in notice_ids}
        self.notices = {**self.notices, **changes}

    def sync(self, broker):
        now, config = broker.clock(), broker.config
        slots = {slot.slot_token: slot for slot in broker.slots.values()}
        source = {(token, notice.notification_id): notice for token, slot in slots.items()
                  for notice in broker._notices(slot)}
        by_token = {}
        for key in source:
            by_token.setdefault(key[0], []).append(key)
        notices = {key: state for key, state in self.notices.items() if key in source}
        orbs = {token: orb for token, orb in self.orbs.items() if token in slots}
        presentation = self.presentation
        selected = broker.selected_slot()
        blocked = bool((selected and broker._held_notice(selected)) or now < broker.hold_until)

        # Terminal identities stay remembered while the underlying notice exists.
        # A question can remain pickup-eligible after attention expires; that is
        # not permission to recreate its visual or replay its onset.
        for key, notice in source.items():
            if notice.status not in _PENDING or now >= notice.expires_at:
                notices[key] = 'removed'
            elif key not in notices:
                notices[key] = 'waiting'

        def covered(token, states=_VISIBLE):
            return [key for key in by_token.get(token, ()) if notices.get(key) in states]

        def make_orb(token, formed_at):
            keys = covered(token)
            if not keys:
                return
            for key in keys:
                notices[key] = 'orb'
            end = min(formed_at + config.orb_hold_seconds + config.orb_fade_seconds,
                      max(source[key].expires_at for key in keys))
            old = orbs.setdefault(token, Orb(secrets.token_hex(12)))
            orbs[token] = replace(old, formed_at=formed_at, ends_at=end, dismissed_at=None)

        if presentation:
            token = presentation.token
            live = tuple(key[1] for key in covered(token, {'presenting'}))
            if token not in slots or not live:
                presentation = None
            else:
                # A genuine new notice from a task already being introduced is
                # covered by that same presentation, rather than another orb.
                additional = tuple(key[1] for key in covered(token, {'waiting'}))
                live += additional
                for notice_id in additional:
                    notices[(token, notice_id)] = 'presenting'
                presentation = replace(presentation, notice_ids=live)
                if blocked and presentation.paused_at is None:
                    presentation = replace(presentation, paused_at=now)
                elif not blocked and presentation.paused_at is not None:
                    presentation = replace(presentation,
                        started_at=presentation.started_at + now - presentation.paused_at,
                        paused_at=None)
                quiet = all(source[(token, notice_id)].controls_dismissed or
                            source[(token, notice_id)].status == 'muted' for notice_id in live)
                completed = presentation.started_at + self._duration(config)
                if quiet or broker.attention_muted(slots[token]):
                    make_orb(token, now)
                    presentation = None
                elif presentation.paused_at is None and now >= completed:
                    make_orb(token, completed)
                    presentation = None

        # Policy-muted arrivals age quietly as orbs. Unmuting does not replay an
        # old attention animation. New notices, not repeated status updates, are
        # the only events allowed to refresh the aggregate task's visual age.
        for token, slot in slots.items():
            waiting = covered(token, {'waiting'})
            if waiting and (broker.attention_muted(slot) or all(
                    source[key].controls_dismissed or source[key].status == 'muted' for key in waiting)):
                make_orb(token, now)

        if presentation is None and not blocked:
            waiting = [key for key, state in notices.items() if state == 'waiting']
            if waiting:
                token, _ = min(waiting, key=lambda key: (source[key].created_at, slots[key[0]].slot_id, key[1]))
                ids = tuple(key[1] for key in covered(token, {'waiting'}))
                presentation = Presentation(token, ids, now, animation=deepcopy(source[(token, ids[0])].animation))
                for notice_id in ids:
                    notices[(token, notice_id)] = 'presenting'
                orb = orbs.setdefault(token, Orb(secrets.token_hex(12)))
                orbs[token] = replace(orb, dismissed_at=None)

        if presentation and presentation.paused_at is None and not blocked:
            # Match normal call promotion: once the deliberate selection hold
            # ends (or a genuinely new call overrides that old hold), its stale
            # static background must not hide an advancing attention animation.
            broker.background_slot = None

        for token, orb in list(orbs.items()):
            keys = covered(token)
            if keys:
                quiet = all(source[key].controls_dismissed or source[key].status == 'muted' for key in keys)
                orb = replace(orb, quiet=quiet)
                if orb.formed_at is not None:
                    end = min(orb.formed_at + config.orb_hold_seconds + config.orb_fade_seconds,
                              max(source[key].expires_at for key in keys))
                    orb = replace(orb, ends_at=end)
                if orb.dismissed_at is not None:
                    orb = replace(orb, dismissed_at=None)
                # A genuinely new presentation can revive this task's body, but
                # old notice IDs stay terminal once their visual lifetime ends.
                if orb.ends_at is not None and now >= orb.ends_at:
                    for key in covered(token, {'orb'}):
                        notices[key] = 'aged_out'
                    if presentation and presentation.token == token:
                        orb = replace(orb, formed_at=None, ends_at=None)
                    else:
                        del orbs[token]
                        continue
            elif orb.formed_at is None:
                # Cancelling/picking up the onset must not invent a fog body
                # that was never formed merely to fade it out afterward.
                del orbs[token]
                continue
            elif orb.dismissed_at is None:
                orb = replace(orb, dismissed_at=now, dismiss_opacity=self._opacity(orb, now, config))
            if orb.dismissed_at is not None and now >= orb.dismissed_at + config.orb_dismiss_seconds:
                del orbs[token]
            else:
                orbs[token] = orb

        self.notices, self.orbs, self.presentation = notices, orbs, presentation

    def snapshot(self, broker):
        now, config = broker.clock(), broker.config
        slots = {slot.slot_token: slot for slot in broker.slots.values()}
        slot_breaths = []
        for token, slot in slots.items():
            if broker.attention_muted(slot):
                continue
            fresh = [notice for notice in broker._notices(slot)
                     if self.notices.get((token, notice.notification_id)) in ('waiting', 'presenting')
                     and notice.status in ('queued', 'active') and not notice.controls_dismissed
                     and now < notice.expires_at
                     and 0 <= now - notice.created_at < config.notification_slot_breath_seconds]
            if fresh:
                newest = max(fresh, key=lambda notice: notice.created_at)
                slot_breaths.append({'slot_id': slot.slot_id, 'elapsed': now - newest.created_at,
                                    'duration': config.notification_slot_breath_seconds})
        presentation = self.presentation
        result = None
        if presentation and presentation.paused_at is None and presentation.token in slots:
            slot, orb = slots[presentation.token], self.orbs[presentation.token]
            elapsed = max(0.0, now - presentation.started_at)
            for phase, seconds in (('onset', NOTIFICATION_ONSET_SECONDS),
                                   ('breathing', config.notification_breath_seconds),
                                   ('condensing', config.orb_formation_seconds)):
                if elapsed < seconds or phase == 'condensing':
                    result = {'slot_id': slot.slot_id, 'identity_color': slot.identity_color,
                              'notice_id': presentation.notice_ids[0], 'phase': phase,
                              'elapsed': min(elapsed, seconds), 'progress': min(1.0, elapsed / seconds),
                              'orb_key': orb.key, 'animation': deepcopy(presentation.animation)}
                    break
                elapsed -= seconds
        orbs = []
        notice_ids = {}
        for key, state in self.notices.items():
            if state in _VISIBLE:
                notice_ids.setdefault(key[0], []).append(key[1])
        for token, orb in self.orbs.items():
            slot = slots.get(token)
            if slot is None:
                continue
            presenting = bool(presentation and presentation.token == token)
            opacity = (1.0 if presenting and presentation.paused_at is None
                       else self._opacity(orb, now, config))
            # Keep the physical body present while hidden so a quiet policy does
            # not reset its position, collide differently, or extend its age.
            hidden = broker.attention_muted(slot)
            orbs.append({'orb_key': orb.key, 'slot_id': slot.slot_id,
                         'identity_color': slot.identity_color,
                         'opacity': 0.0 if hidden else opacity, 'quiet': orb.quiet or hidden,
                         'hidden': hidden, 'presenting': presenting,
                         'formed_at': orb.formed_at,
                         'notice_ids': notice_ids.get(token, [])})
        return {'presentation': result, 'orbs': orbs, 'slot_breaths': slot_breaths,
                'presentation_paused': bool(presentation and presentation.paused_at is not None),
                'queued_tasks': len({key[0] for key, state in self.notices.items() if state == 'waiting'})}
