"""
Local tactics layer: convert planner intent into frame-safe motion.

Uses time-of-closest-approach dodge math plus soft wall push so the bot
never waits on the LLM for micro-reactions.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from echoarena.models import Barrier


@dataclass
class AgentBody:
    x: float = 400.0
    y: float = 300.0
    vx: float = 0.0
    vy: float = 0.0
    hp: int = 100
    plan_dx: float = 0.0
    plan_dy: float = 0.0
    plan_fire: bool = False


@dataclass
class ThreatShot:
    x: float
    y: float
    vx: float
    vy: float
    owner: str


# Tunables — stronger dodge than stock Neural Arena so bullets get cleared more
_BODY_R = 16
_SHOT_R = 5
_HIT_PAD = _BODY_R + _SHOT_R + 14  # wider "will hit" cone
_LOOKAHEAD = 1.85  # react earlier to distant incoming shots
_SOFT_EDGE = 42
_PUSH = 2.2
_DODGE_GAIN = 5.8  # heavier weight on dodge vectors
_PLAN_KEEP = 0.12  # less LLM intent survives while dodging (was 0.30)
_MISS_MULT = 3.2  # treat near-misses as threats (was 2.5)


def _unit(dx: float, dy: float) -> tuple[float, float]:
    mag = math.hypot(dx, dy)
    if mag < 1e-6:
        return 0.0, 0.0
    return dx / mag, dy / mag


def cardinal_clearances(
    body: AgentBody, walls: list[Barrier], arena_w: int, arena_h: int
) -> list[float]:
    """Distances to nearest surface: north, east, south, west."""
    n, e, s, w = body.y, arena_w - body.x, arena_h - body.y, body.x
    for wall in walls:
        x1, y1 = wall.x, wall.y
        x2, y2 = wall.x + wall.w, wall.y + wall.h
        if y2 <= body.y and x1 <= body.x <= x2:
            n = min(n, body.y - y2)
        if y1 >= body.y and x1 <= body.x <= x2:
            s = min(s, y1 - body.y)
        if x2 <= body.x and y1 <= body.y <= y2:
            w = min(w, body.x - x2)
        if x1 >= body.x and y1 <= body.y <= y2:
            e = min(e, x1 - body.x)
    return [n, e, s, w]


def _closest_approach(
    sx: float, sy: float, svx: float, svy: float, tx: float, ty: float
) -> tuple[float, float]:
    rx, ry = sx - tx, sy - ty
    speed2 = svx * svx + svy * svy
    if speed2 < 1e-6:
        return 0.0, math.hypot(rx, ry)
    tca = -(rx * svx + ry * svy) / speed2
    miss = math.hypot(sx + svx * tca - tx, sy + svy * tca - ty)
    return tca, miss


def _soft_push(
    x: float, y: float, walls: list[Barrier], arena_w: int, arena_h: int
) -> tuple[float, float]:
    px, py = 0.0, 0.0
    for dist, dx, dy in (
        (x, 1.0, 0.0),
        (arena_w - x, -1.0, 0.0),
        (y, 0.0, 1.0),
        (arena_h - y, 0.0, -1.0),
    ):
        if dist < _SOFT_EDGE:
            strength = (1.0 - dist / _SOFT_EDGE) * _PUSH
            px += dx * strength
            py += dy * strength

    for wall in walls:
        cx = max(wall.x, min(wall.x + wall.w, x))
        cy = max(wall.y, min(wall.y + wall.h, y))
        dist = math.hypot(x - cx, y - cy)
        if 1e-3 < dist < _SOFT_EDGE:
            strength = (1.0 - dist / _SOFT_EDGE) * _PUSH
            nx, ny = _unit(x - cx, y - cy)
            px += nx * strength
            py += ny * strength
    return px, py


def apply_tactics(
    body: AgentBody,
    incoming: list[ThreatShot],
    walls: list[Barrier],
    move_speed: float,
    arena_w: int,
    arena_h: int,
    dt: float = 1 / 60,
    *,
    fallback_x: float | None = None,
    fallback_y: float | None = None,
) -> tuple[float, float, bool]:
    """Return frame velocity and whether to fire this tick."""
    del dt  # reserved for future predictive smoothing
    dodge_x = dodge_y = 0.0
    threats = 0
    peak = 0.0

    for shot in incoming:
        tca, miss = _closest_approach(shot.x, shot.y, shot.vx, shot.vy, body.x, body.y)
        if tca < -0.05 or tca > _LOOKAHEAD or miss >= _HIT_PAD * _MISS_MULT:
            continue

        tx, ty = _unit(shot.vx, shot.vy)
        left = (ty, -tx)
        right = (-ty, tx)
        at_x = shot.x + shot.vx * max(tca, 0.0)
        at_y = shot.y + shot.vy * max(tca, 0.0)
        side_x, side_y = body.x - at_x, body.y - at_y
        if left[0] * side_x + left[1] * side_y >= 0:
            pick_x, pick_y = left
        else:
            pick_x, pick_y = right

        probe_x, probe_y = _soft_push(
            body.x + pick_x * 40, body.y + pick_y * 40, walls, arena_w, arena_h
        )
        if probe_x * pick_x + probe_y * pick_y < -0.5:
            pick_x, pick_y = -pick_x, -pick_y

        # Imminent shots dominate; slow-arriving ones still nudge sideways
        urgency = _DODGE_GAIN / max(tca, 0.02)
        urgency *= 0.35 + 0.65 * max(0.0, 1.0 - miss / _HIT_PAD)
        if tca < 0.35:
            urgency *= 1.45
        dodge_x += pick_x * urgency
        dodge_y += pick_y * urgency
        threats += 1
        peak = max(peak, urgency)

    plan_dx, plan_dy = body.plan_dx, body.plan_dy
    if abs(plan_dx) + abs(plan_dy) < 0.12 and fallback_x is not None and fallback_y is not None:
        # LLM sent ~0,0 or no intent yet — keep moving toward foe
        plan_dx, plan_dy = _unit(fallback_x - body.x, fallback_y - body.y)

    if threats:
        ndx, ndy = _unit(dodge_x, dodge_y)
        if abs(ndx) + abs(ndy) < 1e-6:
            ndx, ndy = plan_dx, plan_dy
        urgency_scale = min(peak / _DODGE_GAIN, 1.0)
        # Near-certain hits → almost pure dodge
        keep = _PLAN_KEEP * (1.0 - urgency_scale * 0.9)
        if peak >= _DODGE_GAIN * 2.0:
            keep *= 0.35
        dx = ndx * (1.0 - keep) + plan_dx * keep
        dy = ndy * (1.0 - keep) + plan_dy * keep
    else:
        dx, dy = plan_dx, plan_dy

    wx, wy = _soft_push(body.x, body.y, walls, arena_w, arena_h)
    # Stronger wall bias while dodging so we don't slide into cover-traps
    wall_w = 0.7 if threats else 0.5
    dx += wx * wall_w
    dy += wy * wall_w

    # Edge clamps — only kill the into-wall axis, then slide along the free one
    blocked_x = blocked_y = False
    if body.x < _SOFT_EDGE and dx < 0:
        dx = 0.0
        blocked_x = True
    if body.x > arena_w - _SOFT_EDGE and dx > 0:
        dx = 0.0
        blocked_x = True
    if body.y < _SOFT_EDGE and dy < 0:
        dy = 0.0
        blocked_y = True
    if body.y > arena_h - _SOFT_EDGE and dy > 0:
        dy = 0.0
        blocked_y = True

    # Corner / wall pin: motion cancelled → escape along free axis or toward center/foe
    if abs(dx) + abs(dy) < 1e-6:
        if blocked_x and not blocked_y:
            dy = 1.0 if plan_dy >= 0 else -1.0
            if abs(plan_dy) < 0.1:
                dy = 1.0 if body.y < arena_h * 0.5 else -1.0
        elif blocked_y and not blocked_x:
            dx = 1.0 if plan_dx >= 0 else -1.0
            if abs(plan_dx) < 0.1:
                dx = 1.0 if body.x < arena_w * 0.5 else -1.0
        else:
            # Fully pinned or zero plan — push off walls, else chase fallback/center
            if abs(wx) + abs(wy) > 1e-6:
                dx, dy = wx, wy
            elif fallback_x is not None and fallback_y is not None:
                dx, dy = fallback_x - body.x, fallback_y - body.y
            else:
                dx, dy = arena_w * 0.5 - body.x, arena_h * 0.5 - body.y

    dx, dy = _unit(dx, dy)
    if abs(dx) + abs(dy) < 1e-6:
        # Absolute last resort — never return a full stop
        dx, dy = _unit(arena_w * 0.5 - body.x, arena_h * 0.5 - body.y)
        if abs(dx) + abs(dy) < 1e-6:
            dx, dy = 1.0, 0.0

    return dx * move_speed, dy * move_speed, body.plan_fire
