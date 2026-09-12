"""Core combat entities and lightweight visual particles."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Optional

from echoarena.config import (
    FIGHTER_RADIUS,
    HIT_DAMAGE,
    MAX_HEALTH,
    PLAYER_COOLDOWN,
    PROJECTILE_SPEED,
)


@dataclass
class Barrier:
    x: float
    y: float
    w: float
    h: float


@dataclass
class Projectile:
    x: float
    y: float
    vx: float
    vy: float
    owner: str
    age: float = 0.0
    lifespan: float = 2.5

    @property
    def alive(self) -> bool:
        return self.age < self.lifespan


@dataclass
class Fighter:
    x: float
    y: float
    hp: int = MAX_HEALTH
    vx: float = 0.0
    vy: float = 0.0
    cool: float = 0.0

    @property
    def alive(self) -> bool:
        return self.hp > 0

    def ready_to_fire(self) -> bool:
        return self.cool <= 0.0

    def shoot_at(
        self,
        tx: float,
        ty: float,
        owner: str,
        cooldown: float = PLAYER_COOLDOWN,
    ) -> Optional[Projectile]:
        if not self.ready_to_fire():
            return None
        dx = tx - self.x
        dy = ty - self.y
        dist = math.hypot(dx, dy)
        if dist < 1.0:
            return None
        self.cool = cooldown
        return Projectile(
            self.x,
            self.y,
            (dx / dist) * PROJECTILE_SPEED,
            (dy / dist) * PROJECTILE_SPEED,
            owner,
        )

    def overlaps_barrier(self, wall: Barrier) -> bool:
        r = FIGHTER_RADIUS
        return (
            wall.x < self.x + r
            and self.x - r < wall.x + wall.w
            and wall.y < self.y + r
            and self.y - r < wall.y + wall.h
        )

    def take_hit(self, amount: int = HIT_DAMAGE) -> None:
        self.hp = max(0, self.hp - amount)


@dataclass
class Spark:
    x: float
    y: float
    vx: float
    vy: float
    life: float
    max_life: float
    color: tuple[int, int, int]

    @property
    def alive(self) -> bool:
        return self.life > 0


def burst_sparks(x: float, y: float, color: tuple[int, int, int], count: int = 8) -> list[Spark]:
    out: list[Spark] = []
    for _ in range(count):
        ang = random.uniform(0, math.tau)
        spd = random.uniform(60, 180)
        life = random.uniform(0.3, 0.7)
        out.append(
            Spark(x, y, math.cos(ang) * spd, math.sin(ang) * spd, life, life, color)
        )
    return out


def muzzle_sparks(
    x: float, y: float, bvx: float, bvy: float, color: tuple[int, int, int]
) -> list[Spark]:
    out: list[Spark] = []
    base = math.atan2(bvy, bvx)
    for _ in range(5):
        ang = base + random.uniform(-0.4, 0.4)
        spd = random.uniform(40, 120)
        life = random.uniform(0.05, 0.12)
        out.append(
            Spark(x, y, math.cos(ang) * spd, math.sin(ang) * spd, life, life, color)
        )
    return out
