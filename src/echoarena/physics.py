"""Collision helpers and arena bounds."""

from __future__ import annotations

from echoarena.config import ArenaLayout, FIGHTER_RADIUS, SCREEN_H, SCREEN_W
from echoarena.models import Barrier, Fighter, Projectile


def barriers_from_layout(layout: ArenaLayout) -> list[Barrier]:
    return [Barrier(x, y, w, h) for x, y, w, h in layout.blocks]


def projectile_blocked(shot: Projectile, walls: list[Barrier]) -> bool:
    if shot.x < 0 or shot.x > SCREEN_W or shot.y < 0 or shot.y > SCREEN_H:
        return True
    for wall in walls:
        if wall.x <= shot.x <= wall.x + wall.w and wall.y <= shot.y <= wall.y + wall.h:
            return True
    return False


def push_out_of_walls(fighter: Fighter, walls: list[Barrier]) -> None:
    r = FIGHTER_RADIUS
    for wall in walls:
        if not fighter.overlaps_barrier(wall):
            continue
        ox = (fighter.x + r) - wall.x if fighter.vx > 0 else wall.x + wall.w - (fighter.x - r)
        oy = (fighter.y + r) - wall.y if fighter.vy > 0 else wall.y + wall.h - (fighter.y - r)
        if abs(ox) < abs(oy):
            fighter.x = wall.x - r if fighter.vx > 0 else wall.x + wall.w + r
            fighter.vx = 0.0
        else:
            fighter.y = wall.y - r if fighter.vy > 0 else wall.y + wall.h + r
            fighter.vy = 0.0


def keep_inside_arena(fighter: Fighter) -> None:
    r = FIGHTER_RADIUS
    fighter.x = max(r, min(SCREEN_W - r, fighter.x))
    fighter.y = max(r, min(SCREEN_H - r, fighter.y))
