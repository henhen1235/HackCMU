"""EchoArena ↔ agentkit adapters (game-specific mapping only)."""

from __future__ import annotations

import math

from agentkit import ReflexBody, Threat, Wall, WorldSnapshot
from agentkit.runtime import AgentRuntime

from echoarena.config import SCREEN_H, SCREEN_W, ArenaLayout, Difficulty
from echoarena.models import Barrier, Fighter, Projectile


def walls_from_barriers(barriers: list[Barrier]) -> list[Wall]:
    return [Wall(b.x, b.y, b.w, b.h) for b in barriers]


def threats_from_shots(shots: list[Projectile], owner: str = "player") -> list[Threat]:
    return [
        Threat(s.x, s.y, s.vx, s.vy, s.owner)
        for s in shots
        if s.owner == owner
    ]


def compose_snapshot(
    human: Fighter,
    bot: Fighter,
    shots: list[Projectile],
    walls: list[Barrier],
    difficulty: Difficulty,
) -> WorldSnapshot:
    lead_x = max(0.0, min(SCREEN_W, human.x + human.vx * difficulty.lead_time))
    lead_y = max(0.0, min(SCREEN_H, human.y + human.vy * difficulty.lead_time))
    danger = []
    for shot in shots:
        if shot.owner != "player":
            continue
        if math.hypot(shot.x - bot.x, shot.y - bot.y) < 300:
            danger.append(
                {
                    "p": [round(shot.x, 1), round(shot.y, 1)],
                    "v": [round(shot.vx, 1), round(shot.vy, 1)],
                }
            )
    cover = AgentRuntime.cover(
        ReflexBody(x=bot.x, y=bot.y),
        walls_from_barriers(walls),
        SCREEN_W,
        SCREEN_H,
    )
    return WorldSnapshot(
        bot={
            "pos": [round(bot.x, 1), round(bot.y, 1)],
            "vel": [round(bot.vx, 1), round(bot.vy, 1)],
            "hp": bot.hp,
            "armed": bot.ready_to_fire(),
        },
        foe={
            "now": [round(human.x, 1), round(human.y, 1)],
            "vel": [round(human.vx, 1), round(human.vy, 1)],
            "lead": [round(lead_x, 1), round(lead_y, 1)],
            "hp": human.hp,
        },
        danger=danger,
        cover=[round(d, 1) for d in cover],
        notes="unknown",
    )


def seed_snapshot(layout: ArenaLayout) -> WorldSnapshot:
    return WorldSnapshot(
        bot={
            "pos": [layout.bot_spawn[0], layout.bot_spawn[1]],
            "vel": [0, 0],
            "hp": 100,
            "armed": True,
        },
        foe={
            "now": [layout.human_spawn[0], layout.human_spawn[1]],
            "vel": [0, 0],
            "lead": [layout.human_spawn[0], layout.human_spawn[1]],
            "hp": 100,
        },
        danger=[],
        cover=[300, 140, 300, 660],
        notes="unknown",
    )
