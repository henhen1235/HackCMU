"""
Combat brain: LLM strategy when available; competent-but-greedy LOCAL otherwise.

LOCAL is a normal arcade bot (range keep + orbit strafe + always shoot).
It is deliberately predictable so LLM mode looks strategically superior.
"""

from __future__ import annotations

import math
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
    _soft_push,
    _unit,
)


_IDEAL = 200.0
_TOO_FAR = 260.0
_TOO_CLOSE = 130.0


@dataclass
class HuntTarget:
    x: float
    y: float
    vx: float = 0.0
    vy: float = 0.0
    lead_x: float = 0.0
    lead_y: float = 0.0


def _competent_local(body: AgentBody, target: HuntTarget) -> tuple[float, float]:
    """
    Playable LOCAL AI: hold mid-range and orbit. Always the same orbit
    direction so humans (and judges) can read the pattern.
    """
    aim_x = target.lead_x if (target.lead_x or target.lead_y) else target.x
    aim_y = target.lead_y if (target.lead_x or target.lead_y) else target.y
    to_x, to_y = aim_x - body.x, aim_y - body.y
    dist = math.hypot(to_x, to_y) or 1.0
    chase_x, chase_y = to_x / dist, to_y / dist

    # Fixed orbit sense = predictable "normal bot"
    sx, sy = _unit(-chase_y, chase_x)

    if dist > _TOO_FAR:
        # Close in — mostly chase, light strafe
        return _unit(chase_x * 0.9 + sx * 0.35, chase_y * 0.9 + sy * 0.35)
    if dist < _TOO_CLOSE:
        # Create space — mostly retreat, keep orbiting
        return _unit(-chase_x * 0.75 + sx * 0.7, -chase_y * 0.75 + sy * 0.7)
    # Ideal band — circle endlessly (readable, beatable)
    return _unit(chase_x * 0.15 + sx * 1.0, chase_y * 0.15 + sy * 1.0)


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

    LLM mode: planner intent drives motion (reflex only dodges/walls).
    LOCAL mode: competent orbit/strafe brain + same reflex dodge layer.
    """
    aim_x = target.lead_x if (target.lead_x or target.lead_y) else target.x
    aim_y = target.lead_y if (target.lead_x or target.lead_y) else target.y

    llm_mag = abs(body.plan_dx) + abs(body.plan_dy)
    use_llm = has_intent and intent_age_s < INTENT_MAX_AGE_S and llm_mag > 0.05

    aim_chase_x, aim_chase_y = _unit(aim_x - body.x, aim_y - body.y)

    if use_llm:
        # Let the LLM approach walls; reflex soft-push only stops true embeds.
        body.plan_dx, body.plan_dy = _unit(
            body.plan_dx * 0.78 + aim_chase_x * 0.22,
            body.plan_dy * 0.78 + aim_chase_y * 0.22,
        )
    else:
        lx, ly = _competent_local(body, target)
        # Soft wall awareness so LOCAL doesn't pin itself
        wx, wy = _soft_push(body.x, body.y, walls, arena_w, arena_h)
        body.plan_dx, body.plan_dy = _unit(lx + wx * 0.35, ly + wy * 0.35)
        body.plan_fire = True

    threats = 0
    for shot in incoming:
        tca, miss = _closest_approach(shot.x, shot.y, shot.vx, shot.vy, body.x, body.y)
        if tca < -0.05 or tca > _LOOKAHEAD or miss >= _HIT_PAD * _MISS_MULT:
            continue
        threats += 1

    vx, vy, should_fire = apply_tactics(
        body,
        incoming,
        walls,
        move_speed,
        arena_w,
        arena_h,
        fallback_x=aim_x,
        fallback_y=aim_y,
    )

    under_threat = threats > 0
    dodging = threats > 0
    fire = bool(body.plan_fire if use_llm else True)
    return vx, vy, fire, use_llm, under_threat, dodging
