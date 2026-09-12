"""Shared constants, difficulty tiers, and arena layouts."""

from __future__ import annotations

from dataclasses import dataclass


SCREEN_W = 800
SCREEN_H = 600
FPS = 60
WINDOW_TITLE = "EchoArena — Human vs Adaptive Bot"

PLAYER_SPEED = 200.0
BOT_SPEED = PLAYER_SPEED
PROJECTILE_SPEED = 420.0
PROJECTILE_RADIUS = 5
FIGHTER_RADIUS = 16
PLAYER_COOLDOWN = 0.22
BOT_COOLDOWN = PLAYER_COOLDOWN
HIT_DAMAGE = 10
MAX_HEALTH = 100
# Same aim rule for both: no bot-only prediction advantage
BOT_LEAD_TIME = 0.0

TELEMETRY_INTERVAL = 0.25
# Grok is fast + rarely rate-limits — denser pipeline for fresher thinking
PLANNER_INTERVAL = 0.2
PLANNER_MAX_IN_FLIGHT = 8
PLANNER_WORKERS = 8
PLANNER_TIMEOUT_S = 6.0
PLANNER_MAX_TOKENS = 300
PLANNER_429_BACKOFF_S = 4.0
# Drop stale LLM packets quickly so the bot doesn't ride an old plan
INTENT_MAX_AGE_S = 1.5
DEFAULT_LATENCY_MS = 300
MIN_WALL_CLEARANCE = 50
PROFILE_PATH_REL = ("data", "player_profile.txt")


@dataclass(frozen=True)
class Difficulty:
    label: str
    bot_speed: float
    bot_cooldown: float
    lead_time: float
    accent: tuple[int, int, int]


# Every tier uses the exact same combat rules as the human
DIFFICULTIES: list[Difficulty] = [
    Difficulty("CALM", BOT_SPEED, BOT_COOLDOWN, BOT_LEAD_TIME, (120, 160, 110)),
    Difficulty("STEADY", BOT_SPEED, BOT_COOLDOWN, BOT_LEAD_TIME, (180, 140, 70)),
    Difficulty("SHARP", BOT_SPEED, BOT_COOLDOWN, BOT_LEAD_TIME, (200, 100, 60)),
    Difficulty("RELENTLESS", BOT_SPEED, BOT_COOLDOWN, BOT_LEAD_TIME, (190, 70, 50)),
]


@dataclass(frozen=True)
class ArenaLayout:
    label: str
    blocks: list[tuple[int, int, int, int]]
    human_spawn: tuple[int, int]
    bot_spawn: tuple[int, int]
    floor: tuple[int, int, int]
    steel: tuple[int, int, int]
    rim: tuple[int, int, int]


# Amber / steel palette (not neon cyberpunk)
PALETTE = {
    "bg": (18, 16, 14),
    "steel": (70, 62, 52),
    "rim": (210, 150, 70),
    "human": (220, 180, 90),
    "human_shade": (160, 120, 50),
    "bot": (180, 70, 55),
    "bot_shade": (120, 40, 35),
    "shot_human": (240, 200, 110),
    "shot_bot": (220, 100, 70),
    "hp_ok": (140, 180, 90),
    "hp_low": (200, 80, 60),
    "hp_track": (30, 26, 22),
    "ink": (230, 220, 200),
    "accent": (210, 150, 70),
    "flash": (255, 160, 100),
    "think": (200, 175, 120),
}


ARENAS: list[ArenaLayout] = [
    ArenaLayout(
        label="CROSSFIRE",
        blocks=[
            (140, 90, 90, 120),
            (340, 140, 120, 45),
            (570, 90, 90, 120),
            (100, 310, 150, 45),
            (550, 310, 150, 45),
            (290, 260, 220, 45),
            (140, 410, 90, 120),
            (570, 410, 90, 120),
            (330, 440, 140, 45),
        ],
        human_spawn=(140, 300),
        bot_spawn=(660, 300),
        floor=(16, 14, 12),
        steel=(75, 65, 50),
        rim=(200, 145, 65),
    ),
    ArenaLayout(
        label="WIDE FLATS",
        blocks=[
            (360, 210, 80, 180),
            (130, 140, 55, 70),
            (615, 140, 55, 70),
            (180, 460, 440, 35),
        ],
        human_spawn=(90, 90),
        bot_spawn=(710, 90),
        floor=(14, 16, 13),
        steel=(85, 75, 55),
        rim=(180, 160, 80),
    ),
    ArenaLayout(
        label="SWITCHBACK",
        blocks=[
            (70, 40, 280, 35),
            (450, 40, 280, 35),
            (70, 140, 35, 280),
            (695, 140, 35, 280),
            (160, 190, 110, 40),
            (530, 190, 110, 40),
            (240, 290, 320, 40),
            (160, 390, 110, 40),
            (530, 390, 110, 40),
            (280, 140, 45, 110),
            (470, 200, 45, 140),
            (180, 460, 440, 40),
        ],
        human_spawn=(110, 90),
        bot_spawn=(690, 510),
        floor=(20, 15, 14),
        steel=(90, 55, 45),
        rim=(210, 120, 70),
    ),
]
