"""Amber / steel rendering, menus, and HUD."""

from __future__ import annotations

import math

import pygame

from echoarena.config import (
    ARENAS,
    DIFFICULTIES,
    PALETTE,
    SCREEN_H,
    SCREEN_W,
    ArenaLayout,
)
from echoarena.models import Barrier, Fighter, Projectile, Spark

_FONT_SM: pygame.font.Font | None = None
_FONT_MD: pygame.font.Font | None = None
_FONT_LG: pygame.font.Font | None = None
_GRID: pygame.Surface | None = None
_SHOT_GLOW_H: pygame.Surface | None = None
_SHOT_GLOW_B: pygame.Surface | None = None


def warm_caches() -> None:
    global _FONT_SM, _FONT_MD, _FONT_LG, _GRID, _SHOT_GLOW_H, _SHOT_GLOW_B
    _FONT_SM = pygame.font.SysFont("georgia", 14)
    _FONT_MD = pygame.font.SysFont("georgia", 18, bold=True)
    _FONT_LG = pygame.font.SysFont("georgia", 42, bold=True)

    _GRID = pygame.Surface((SCREEN_W, SCREEN_H), pygame.SRCALPHA)
    for gx in range(0, SCREEN_W, 40):
        pygame.draw.line(_GRID, (40, 34, 28, 255), (gx, 0), (gx, SCREEN_H))
    for gy in range(0, SCREEN_H, 40):
        pygame.draw.line(_GRID, (40, 34, 28, 255), (0, gy), (SCREEN_W, gy))

    sz = 40
    _SHOT_GLOW_H = pygame.Surface((sz, sz), pygame.SRCALPHA)
    _SHOT_GLOW_B = pygame.Surface((sz, sz), pygame.SRCALPHA)
    ch = PALETTE["shot_human"]
    cb = PALETTE["shot_bot"]
    for r, a in ((12, 110), (16, 55), (20, 20)):
        pygame.draw.circle(_SHOT_GLOW_H, (*ch, a), (sz // 2, sz // 2), r)
        pygame.draw.circle(_SHOT_GLOW_B, (*cb, a), (sz // 2, sz // 2), r)


def paint_barriers(surf: pygame.Surface, walls: list[Barrier], layout: ArenaLayout) -> None:
    for wall in walls:
        pygame.draw.rect(surf, layout.steel, (wall.x, wall.y, wall.w, wall.h))
        pygame.draw.rect(surf, layout.rim, (wall.x, wall.y, wall.w, wall.h), 2)


def paint_fighter(
    surf: pygame.Surface,
    body: Fighter,
    color: tuple[int, int, int],
    shade: tuple[int, int, int],
    tag: str,
) -> None:
    r = 16
    glow = pygame.Surface((r * 6, r * 6), pygame.SRCALPHA)
    pygame.draw.circle(glow, (*color, 55), (r * 3, r * 3), int(r * 2.2))
    surf.blit(glow, (int(body.x - r * 3), int(body.y - r * 3)))
    pygame.draw.circle(surf, shade, (int(body.x), int(body.y)), r + 2)
    pygame.draw.circle(surf, color, (int(body.x), int(body.y)), r)
    pygame.draw.circle(surf, (250, 235, 200), (int(body.x), int(body.y)), 4)
    label = (_FONT_SM or pygame.font.SysFont("georgia", 14)).render(tag, True, PALETTE["ink"])
    surf.blit(label, (int(body.x) - label.get_width() // 2, int(body.y) + r + 4))


def paint_shot(surf: pygame.Surface, shot: Projectile) -> None:
    glow = _SHOT_GLOW_H if shot.owner == "player" else _SHOT_GLOW_B
    color = PALETTE["shot_human"] if shot.owner == "player" else PALETTE["shot_bot"]
    if glow:
        surf.blit(glow, (int(shot.x - glow.get_width() / 2), int(shot.y - glow.get_height() / 2)))
    pygame.draw.circle(surf, color, (int(shot.x), int(shot.y)), 5)
    pygame.draw.circle(surf, (255, 240, 210), (int(shot.x), int(shot.y)), 2)


def paint_sparks(surf: pygame.Surface, sparks: list[Spark]) -> None:
    for spark in sparks:
        alpha = max(0.0, spark.life / spark.max_life)
        r = max(1, int(3 * alpha))
        pygame.draw.circle(surf, spark.color, (int(spark.x), int(spark.y)), r)


def paint_aim(surf: pygame.Surface, x: float, y: float, mx: int, my: int) -> None:
    dx, dy = mx - x, my - y
    dist = math.hypot(dx, dy) or 1.0
    nx, ny = dx / dist, dy / dist
    for i in range(8, 90, 8):
        pygame.draw.circle(
            surf,
            (90, 70, 40),
            (int(x + nx * i), int(y + ny * i)),
            1,
        )


def _hp_bar(surf: pygame.Surface, x: int, y: int, hp: int, w: int, label: str, color: tuple) -> None:
    font = _FONT_SM or pygame.font.SysFont("georgia", 14)
    pygame.draw.rect(surf, PALETTE["hp_track"], (x, y, w, 14))
    pct = max(0.0, min(1.0, hp / 100))
    bar = color if hp > 35 else PALETTE["hp_low"]
    pygame.draw.rect(surf, bar, (x, y, int(w * pct), 14))
    pygame.draw.rect(surf, (70, 60, 50), (x, y, w, 14), 1)
    surf.blit(font.render(f"{label} {hp}", True, PALETTE["ink"]), (x, y - 16))


def paint_hud(
    surf: pygame.Surface,
    human: Fighter,
    bot: Fighter,
    intent: tuple[float, float, bool],
    notes: list[str],
    fps: float,
    flash: float,
    layout: ArenaLayout,
) -> None:
    del layout
    _hp_bar(surf, 16, 28, human.hp, 140, "YOU", PALETTE["human"])
    _hp_bar(surf, SCREEN_W - 156, 28, bot.hp, 140, "ECHO", PALETTE["bot"])

    font = _FONT_SM or pygame.font.SysFont("georgia", 14)
    mx, my, fire = intent
    panel = pygame.Surface((230, 130), pygame.SRCALPHA)
    pygame.draw.rect(panel, (20, 16, 12, 180), (0, 0, 230, 130), border_radius=6)
    pygame.draw.rect(panel, (*PALETTE["rim"], 180), (0, 0, 230, 130), 2, border_radius=6)
    lines = [
        f"intent  mx={mx:+.2f}  my={my:+.2f}",
        f"fire    {'YES' if fire else 'no'}",
        f"fps     {fps:.0f}",
        "notes:",
    ]
    y = 8
    for line in lines:
        panel.blit(font.render(line, True, PALETTE["think"]), (10, y))
        y += 16
    for note in notes[-4:]:
        clipped = note[:34] + ("…" if len(note) > 34 else "")
        panel.blit(font.render(clipped, True, PALETTE["ink"]), (10, y))
        y += 14
    surf.blit(panel, (SCREEN_W - 246, SCREEN_H - 146))

    if flash > 0:
        veil = pygame.Surface((SCREEN_W, SCREEN_H), pygame.SRCALPHA)
        veil.fill((*PALETTE["flash"], int(90 * flash / 0.35)))
        surf.blit(veil, (0, 0))


def show_game_over(surf: pygame.Surface, winner: str, status: str = "") -> None:
    big = pygame.font.SysFont("georgia", 64, bold=True)
    small = pygame.font.SysFont("georgia", 22)
    tiny = pygame.font.SysFont("georgia", 16)
    msg = "YOU WIN" if winner == "player" else "ECHO WINS"
    color = PALETTE["human"] if winner == "player" else PALETTE["bot"]
    overlay = pygame.Surface((SCREEN_W, SCREEN_H), pygame.SRCALPHA)
    overlay.fill((10, 8, 6, 160))
    surf.blit(overlay, (0, 0))
    box = pygame.Rect(150, 170, 500, 260)
    pygame.draw.rect(surf, (28, 22, 18), box, border_radius=10)
    pygame.draw.rect(surf, color, box, 3, border_radius=10)
    title = big.render(msg, True, color)
    surf.blit(title, title.get_rect(center=(SCREEN_W // 2, 240)))
    hint = small.render("Saving memory… then back to menu", True, PALETTE["ink"])
    surf.blit(hint, hint.get_rect(center=(SCREEN_W // 2, 320)))
    sub = tiny.render("ENTER continue   ESC quit", True, PALETTE["think"])
    surf.blit(sub, sub.get_rect(center=(SCREEN_W // 2, 360)))
    if status:
        st = tiny.render(status, True, PALETTE["accent"])
        surf.blit(st, st.get_rect(center=(SCREEN_W // 2, 395)))
    pygame.display.flip()


def pick_difficulty(surf: pygame.Surface) -> int | None:
    """
    Display difficulty selection screen.
    Returns selected index, or None if the window was closed.
    """
    title_f = pygame.font.SysFont("georgia", 48, bold=True)
    body_f = pygame.font.SysFont("georgia", 18, bold=True)
    hint_f = pygame.font.SysFont("georgia", 14)
    chosen = 1
    picking = True
    while picking:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return None
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    return chosen
                if event.key in (pygame.K_1, pygame.K_2, pygame.K_3, pygame.K_4):
                    chosen = event.key - pygame.K_1
                    picking = False

        surf.fill(PALETTE["bg"])
        title = title_f.render("CHOOSE PRESSURE", True, PALETTE["accent"])
        surf.blit(title, title.get_rect(center=(SCREEN_W // 2, 50)))
        for i, diff in enumerate(DIFFICULTIES):
            box = pygame.Rect(20 + i * 195, 130, 180, 300)
            active = i == chosen
            pygame.draw.rect(surf, (32, 26, 20), box, border_radius=8)
            pygame.draw.rect(
                surf, diff.accent if active else (80, 70, 55), box, 3 if active else 1, border_radius=8
            )
            name = body_f.render(diff.label, True, diff.accent)
            surf.blit(name, name.get_rect(center=(box.centerx, box.y + 36)))
            stats = [
                "same speed",
                "same fire rate",
                "same damage",
                f"key [{i + 1}]",
            ]
            y = box.y + 90
            for line in stats:
                t = hint_f.render(line, True, PALETTE["ink"])
                surf.blit(t, (box.x + 18, y))
                y += 28
        foot = hint_f.render("1–4 select   ESC confirm Steady default", True, PALETTE["accent"])
        surf.blit(foot, foot.get_rect(center=(SCREEN_W // 2, SCREEN_H - 36)))
        pygame.display.flip()
        pygame.time.wait(16)
    return chosen


def pick_arena(surf: pygame.Surface) -> int | None:
    """
    Display arena selection. Returns index, or None if window closed.
    """
    title_f = pygame.font.SysFont("georgia", 48, bold=True)
    body_f = pygame.font.SysFont("georgia", 20, bold=True)
    hint_f = pygame.font.SysFont("georgia", 14)
    chosen = 0
    picking = True
    while picking:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return None
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    return chosen
                if event.key in (pygame.K_1, pygame.K_2, pygame.K_3):
                    chosen = event.key - pygame.K_1
                    picking = False

        surf.fill(PALETTE["bg"])
        title = title_f.render("CHOOSE ARENA", True, PALETTE["accent"])
        surf.blit(title, title.get_rect(center=(SCREEN_W // 2, 40)))
        for i, layout in enumerate(ARENAS):
            box = pygame.Rect(40 + i * 250, 110, 230, 340)
            active = i == chosen
            pygame.draw.rect(surf, (30, 24, 18), box, border_radius=8)
            pygame.draw.rect(
                surf,
                PALETTE["accent"] if active else (70, 60, 50),
                box,
                4 if active else 1,
                border_radius=8,
            )
            sx = (box.w - 20) / SCREEN_W
            sy = (box.h - 70) / SCREEN_H
            for x, y, w, h in layout.blocks:
                pygame.draw.rect(
                    surf,
                    layout.steel,
                    (
                        box.x + 10 + int(x * sx),
                        box.y + 10 + int(y * sy),
                        max(1, int(w * sx)),
                        max(1, int(h * sy)),
                    ),
                )
            pygame.draw.circle(
                surf,
                PALETTE["human"],
                (
                    box.x + 10 + int(layout.human_spawn[0] * sx),
                    box.y + 10 + int(layout.human_spawn[1] * sy),
                ),
                3,
            )
            pygame.draw.circle(
                surf,
                PALETTE["bot"],
                (
                    box.x + 10 + int(layout.bot_spawn[0] * sx),
                    box.y + 10 + int(layout.bot_spawn[1] * sy),
                ),
                3,
            )
            name = body_f.render(layout.label, True, PALETTE["accent"])
            surf.blit(name, (box.x + 14, box.bottom - 48))
            key = hint_f.render(f"Press [{i + 1}]", True, PALETTE["ink"])
            surf.blit(key, (box.x + 14, box.bottom - 26))
        foot = hint_f.render("1–3 select arena", True, PALETTE["accent"])
        surf.blit(foot, foot.get_rect(center=(SCREEN_W // 2, SCREEN_H - 28)))
        pygame.display.flip()
        pygame.time.wait(16)
    return chosen


def arena_curtain(surf: pygame.Surface, layout: ArenaLayout, seconds: float = 1.5) -> None:
    title_f = pygame.font.SysFont("georgia", 40, bold=True)
    start = pygame.time.get_ticks() / 1000.0
    while (pygame.time.get_ticks() / 1000.0) - start < seconds:
        for event in pygame.event.get():
            if event.type in (pygame.QUIT, pygame.KEYDOWN):
                return
        elapsed = (pygame.time.get_ticks() / 1000.0) - start
        surf.fill(layout.floor)
        for x, y, w, h in layout.blocks:
            pygame.draw.rect(surf, layout.steel, (x, y, w, h))
            pygame.draw.rect(surf, layout.rim, (x, y, w, h), 2)
        pulse = 0.5 + 0.5 * math.sin(elapsed * 6)
        pygame.draw.circle(
            surf,
            PALETTE["human"],
            layout.human_spawn,
            int(10 + 4 * pulse),
        )
        pygame.draw.circle(
            surf,
            PALETTE["bot"],
            layout.bot_spawn,
            int(10 + 4 * pulse),
        )
        title = title_f.render(layout.label, True, PALETTE["accent"])
        surf.blit(title, title.get_rect(center=(SCREEN_W // 2, 40)))
        pygame.display.flip()
        pygame.time.wait(16)


def blit_floor(surf: pygame.Surface, layout: ArenaLayout) -> None:
    surf.fill(layout.floor)
    if _GRID:
        surf.blit(_GRID, (0, 0))
