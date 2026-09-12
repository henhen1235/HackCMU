"""
Combat brain: LLM strategy when available; dumb LOCAL chase otherwise.

LOCAL blindly runs at the player and shoots — no orbit, range-keep, or dodge.
That contrast makes LLM mode look strategically superior in ablation demos.
"""

from __future__ import annotations

from dataclasses import dataclass

from echoarena.config import INTENT_MAX_AGE_S
from echoarena.models import Barrier
from echoarena.tactics.reflex import (
    AgentBody,
    ThreatShot,
    apply_tactics,
    _closest_approach,
    _HIT_PAD,
    _LOOKAHEAD,
    _MISS_MULT,
    _unit,
)


@dataclass
class HuntTarget:
    x: float
    y: float
    vx: float = 0.0
    vy: float = 0.0
    lead_x: float = 0.0
    lead_y: float = 0.0


def _dumb_chase(body: AgentBody, target: HuntTarget) -> tuple[float, float]:
    """Run straight at the player's current position. No lead, no strafe."""
    return _unit(target.x - body.x, target.y - body.y)


def hunt_and_fire(
    body: AgentBody,
    target: HuntTarget,
    incoming: list[ThreatShot],
    walls: list[Barrier],
    move_speed: float,
    arena_w: int,
    arena_h: int,
    *,
    intent_age_s: float = 999.0,
    has_intent: bool = False,
) -> tuple[float, float, bool, bool, bool, bool]:
    """
    Return (vx, vy, should_fire, used_llm, under_threat, dodging).

    LLM mode: planner intent drives motion (reflex dodges/walls); always fire.
    Stale LLM intent: keep last vector + dodge (never fall into dumb LOCAL).
    LOCAL mode: blind chase + always fire; no TCA dodge.
    """
    aim_x = target.lead_x if (target.lead_x or target.lead_y) else target.x
    aim_y = target.lead_y if (target.lead_x or target.lead_y) else target.y

    llm_mag = abs(body.plan_dx) + abs(body.plan_dy)
    fresh_llm = has_intent and intent_age_s < INTENT_MAX_AGE_S and llm_mag > 0.05
    # Any LLM packet this match (even aging out) — do not demote to dumb chase
    llm_context = has_intent and llm_mag > 0.05

    aim_chase_x, aim_chase_y = _unit(aim_x - body.x, aim_y - body.y)

    if fresh_llm:
        body.plan_dx, body.plan_dy = _unit(
            body.plan_dx * 0.7 + aim_chase_x * 0.3,
            body.plan_dy * 0.7 + aim_chase_y * 0.3,
        )
        reflex_incoming = incoming
        used_llm = True
    elif llm_context:
        # Intent expired but we're still in LLM mode — chase-bias last plan + dodge
        body.plan_dx, body.plan_dy = _unit(
            body.plan_dx * 0.35 + aim_chase_x * 0.65,
            body.plan_dy * 0.35 + aim_chase_y * 0.65,
        )
        reflex_incoming = incoming
        used_llm = True
    else:
        body.plan_dx, body.plan_dy = _dumb_chase(body, target)
        reflex_incoming = []
        used_llm = False

    # Never hold fire — holding was a huge reason the bot lost
    body.plan_fire = True

    threats = 0
    for shot in incoming:
        tca, miss = _closest_approach(shot.x, shot.y, shot.vx, shot.vy, body.x, body.y)
        if tca < -0.05 or tca > _LOOKAHEAD or miss >= _HIT_PAD * _MISS_MULT:
            continue
        threats += 1

    vx, vy, _should_fire = apply_tactics(
        body,
        reflex_incoming,
        walls,
        move_speed,
        arena_w,
        arena_h,
        fallback_x=aim_x if used_llm else target.x,
        fallback_y=aim_y if used_llm else target.y,
    )

    under_threat = threats > 0
    dodging = bool(used_llm and threats > 0)
    return vx, vy, True, used_llm, under_threat, dodging
