"""
Optional 2D reflex layer: convert planner intent into frame-safe motion.

Uses time-of-closest-approach dodge math plus soft wall push so the bot
never waits on the LLM for micro-reactions. Games that are not 2D shooters
can ignore this module and apply Intent themselves.
"""

from __future__ import annotations

import math

from agentkit.contracts import ReflexBody, Threat, Wall

_BODY_R = 16
_SHOT_R = 5
_HIT_PAD = _BODY_R + _SHOT_R + 8
_LOOKAHEAD = 1.2
_SOFT_EDGE = 38
_PUSH = 1.8
_DODGE_GAIN = 3.5
_PLAN_KEEP = 0.30


def _unit(dx: float, dy: float) -> tuple[float, float]:
    mag = math.hypot(dx, dy)
    if mag < 1e-6:
        return 0.0, 0.0
    return dx / mag, dy / mag


def cardinal_clearances(
    body: ReflexBody, walls: list[Wall], arena_w: int, arena_h: int
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
    x: float, y: float, walls: list[Wall], arena_w: int, arena_h: int
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


def apply_reflex(
    body: ReflexBody,
    incoming: list[Threat],
    walls: list[Wall],
    move_speed: float,
    arena_w: int,
    arena_h: int,
    dt: float = 1 / 60,
) -> tuple[float, float, bool]:
    """Return frame velocity (vx, vy) and whether to fire this tick."""
    del dt
    dodge_x = dodge_y = 0.0
    threats = 0
    peak = 0.0

    for shot in incoming:
        tca, miss = _closest_approach(shot.x, shot.y, shot.vx, shot.vy, body.x, body.y)
        if tca < -0.05 or tca > _LOOKAHEAD or miss >= _HIT_PAD * 2.5:
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
            body.x + pick_x * 30, body.y + pick_y * 30, walls, arena_w, arena_h
        )
        if probe_x * pick_x + probe_y * pick_y < -0.5:
            pick_x, pick_y = -pick_x, -pick_y

        urgency = _DODGE_GAIN / max(tca, 0.03)
        urgency *= 0.4 + 0.6 * max(0.0, 1.0 - miss / _HIT_PAD)
        dodge_x += pick_x * urgency
        dodge_y += pick_y * urgency
        threats += 1
        peak = max(peak, urgency)

    if threats:
        ndx, ndy = _unit(dodge_x, dodge_y)
        urgency_scale = min(peak / _DODGE_GAIN, 1.0)
        keep = _PLAN_KEEP * (1.0 - urgency_scale * 0.7)
        dx = ndx * (1.0 - keep) + body.plan_dx * keep
        dy = ndy * (1.0 - keep) + body.plan_dy * keep
    else:
        dx, dy = body.plan_dx, body.plan_dy

    wx, wy = _soft_push(body.x, body.y, walls, arena_w, arena_h)
    dx += wx * 0.5
    dy += wy * 0.5

    if body.x < _SOFT_EDGE and dx < 0:
        dx = 0.0
    if body.x > arena_w - _SOFT_EDGE and dx > 0:
        dx = 0.0
    if body.y < _SOFT_EDGE and dy < 0:
        dy = 0.0
    if body.y > arena_h - _SOFT_EDGE and dy > 0:
        dy = 0.0

    dx, dy = _unit(dx, dy)
    return dx * move_speed, dy * move_speed, body.plan_fire
