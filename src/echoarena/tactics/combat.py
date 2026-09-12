"""
Local combat brain — original Neural Arena reflex contract.

LLM intent IS movement when safe. Local code only:
  - perpendicular dodge on imminent bullets
  - soft wall / edge push
Never run a parallel "local hunt" that overrides strategy.
"""

from __future__ import annotations

from dataclasses import dataclass

from echoarena.models import Barrier
from echoarena.tactics.reflex import (
    AgentBody,
    ThreatShot,
    apply_tactics,
    _closest_approach,
    _HIT_PAD,
    _LOOKAHEAD,
    _MISS_MULT,
)


@dataclass
class HuntTarget:
    """Kept for call-site compatibility; movement no longer uses local hunt."""

    x: float
    y: float
    vx: float = 0.0
    vy: float = 0.0
    lead_x: float = 0.0
    lead_y: float = 0.0


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

    Matches original process_reflex: intent drives motion; dodge blends only
    when bullets threaten. Never fully stops — chases foe if intent is empty
    or walls cancel the vector.
    """
    aim_x = target.lead_x if (target.lead_x or target.lead_y) else target.x
    aim_y = target.lead_y if (target.lead_x or target.lead_y) else target.y

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

    llm_mag = abs(body.plan_dx) + abs(body.plan_dy)
    used_llm = has_intent and intent_age_s < 8.0 and llm_mag > 0.05
    under_threat = threats > 0
    dodging = threats > 0
    # Always honor LLM shoot intent (original reflex never suppressed it)
    return vx, vy, bool(body.plan_fire if has_intent else should_fire), used_llm, under_threat, dodging
