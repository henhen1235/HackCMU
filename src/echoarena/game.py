"""EchoArena main loop — 60 FPS simulation bridged to an async planner thread."""

from __future__ import annotations

import asyncio
import math
import sys
import threading
import time
from typing import Optional

import pygame

from echoarena.agent.memory import load_profile, persist_match
from echoarena.agent.planner import run_planner
from echoarena.config import (
    ARENAS,
    BOT_COOLDOWN,
    BOT_SPEED,
    DEFAULT_LATENCY_MS,
    DIFFICULTIES,
    FPS,
    MIN_WALL_CLEARANCE,
    PALETTE,
    PLANNER_429_BACKOFF_S,
    PLANNER_INTERVAL,
    PLANNER_MAX_IN_FLIGHT,
    PLANNER_MAX_TOKENS,
    PLANNER_TIMEOUT_S,
    PLANNER_WORKERS,
    PLAYER_SPEED,
    SCREEN_H,
    SCREEN_W,
    TELEMETRY_INTERVAL,
    WINDOW_TITLE,
    ArenaLayout,
    Difficulty,
)
from echoarena.metrics import METRICS
from echoarena.metrics_dashboard import MetricsWindow
from echoarena.models import Fighter, Projectile, Spark, burst_sparks, muzzle_sparks
from echoarena.physics import (
    barriers_from_layout,
    keep_inside_arena,
    projectile_blocked,
    push_out_of_walls,
)
from echoarena.render import (
    blit_floor,
    paint_aim,
    paint_barriers,
    paint_fighter,
    paint_hud,
    paint_shot,
    paint_sparks,
    pick_arena,
    pick_difficulty,
    show_game_over,
    warm_caches,
)
from echoarena.tactics.combat import HuntTarget, hunt_and_fire
from echoarena.tactics.reflex import (
    AgentBody,
    ThreatShot,
    border_clearances,
    cover_clearances,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


class Bridge:
    """Thread-safe handoff between the pygame loop and the planner thread."""

    def __init__(self) -> None:
        self._snap_lock = threading.Lock()
        self._snap: dict = {}
        self._intent_lock = threading.Lock()
        self.mx = 0.0
        self.my = 0.0
        self.fire = True
        self.intent_at = 0.0
        self.has_intent = False
        self.latest_note = ""
        self._notes_lock = threading.Lock()
        self.notes: list[str] = []

    def publish_snap(self, data: dict) -> None:
        with self._snap_lock:
            self._snap = data

    def read_snap(self) -> dict:
        with self._snap_lock:
            return dict(self._snap)

    def accept_intent(self, decision: dict) -> None:
        dx = float(decision.get("dx", 0.0))
        dy = float(decision.get("dy", 0.0))
        with self._intent_lock:
            # Ignore near-zero move packets so a bad LLM tick can't freeze the bot
            if abs(dx) + abs(dy) >= 0.15:
                self.mx = dx
                self.my = dy
            self.fire = bool(decision.get("shoot", True))
            self.intent_at = time.monotonic()
            self.has_intent = True

    def read_intent(self) -> tuple[float, float, bool, float, bool]:
        with self._intent_lock:
            age = time.monotonic() - self.intent_at if self.has_intent else 999.0
            return self.mx, self.my, self.fire, age, self.has_intent

    def push_note(self, text: str) -> None:
        with self._notes_lock:
            self.notes.append(text)
            if len(self.notes) > 200:
                self.notes = self.notes[-200:]

    def dump_notes(self) -> list[str]:
        with self._notes_lock:
            return list(self.notes)

    def clear_notes(self) -> None:
        with self._notes_lock:
            self.notes = []


class PlannerWorker(threading.Thread):
    def __init__(
        self,
        bridge: Bridge,
        stop_flag: threading.Event,
        pause_flag: threading.Event,
        on_note,
        profile: str,
        difficulty: Difficulty,
    ) -> None:
        super().__init__(daemon=True)
        self.bridge = bridge
        self.stop_flag = stop_flag
        self.pause_flag = pause_flag
        self.on_note = on_note
        self.profile = profile
        self.difficulty = difficulty

    def run(self) -> None:
        async def _drive() -> None:
            async_stop = asyncio.Event()
            loop = asyncio.get_running_loop()

            def _poll() -> None:
                if self.stop_flag.is_set():
                    async_stop.set()
                else:
                    loop.call_later(0.05, _poll)

            loop.call_soon(_poll)
            await run_planner(
                get_snapshot=self.bridge.read_snap,
                on_intent=self.bridge.accept_intent,
                on_note=self.on_note,
                map_width=SCREEN_W,
                map_height=SCREEN_H,
                latency_ms=DEFAULT_LATENCY_MS,
                min_wall_distance=MIN_WALL_CLEARANCE,
                workers=PLANNER_WORKERS,
                interval=PLANNER_INTERVAL,
                max_in_flight=PLANNER_MAX_IN_FLIGHT,
                max_tokens=PLANNER_MAX_TOKENS,
                timeout_s=PLANNER_TIMEOUT_S,
                backoff_429_s=PLANNER_429_BACKOFF_S,
                stop_event=async_stop,
                pause_event=self.pause_flag,
                player_memory=self.profile,
            )

        asyncio.run(_drive())


def compose_snapshot(
    human: Fighter,
    bot: Fighter,
    shots: list[Projectile],
    walls,
    difficulty: Difficulty,
) -> dict:
    # Fair lead: difficulty.lead_time is 0 — predicted_pos == current unless tiers change
    pred_x = max(0.0, min(SCREEN_W, human.x + human.vx * difficulty.lead_time))
    pred_y = max(0.0, min(SCREEN_H, human.y + human.vy * difficulty.lead_time))
    threats = []
    for shot in shots:
        if shot.owner != "player":
            continue
        if math.hypot(shot.x - bot.x, shot.y - bot.y) < 300:
            threats.append(
                {
                    "p": [round(shot.x, 1), round(shot.y, 1)],
                    "v": [round(shot.vx, 1), round(shot.vy, 1)],
                }
            )
    borders = border_clearances(bot.x, bot.y, SCREEN_W, SCREEN_H)
    cover = cover_clearances(AgentBody(x=bot.x, y=bot.y), walls)
    to_x = pred_x - bot.x
    to_y = pred_y - bot.y
    dist = math.hypot(to_x, to_y) or 1.0
    if dist < 130:
        band = "close"
    elif dist < 260:
        band = "mid"
    else:
        band = "far"
    # Borders = map edges (NOT cover). Cover = interior blocks only.
    border_pin = [
        name for name, d in zip(("N", "E", "S", "W"), borders) if d < 28
    ]
    near_cover = [
        name for name, d in zip(("N", "E", "S", "W"), cover) if d < 90
    ]
    ev_spd = math.hypot(human.vx, human.vy)
    return {
        "bot": {
            "pos": [round(bot.x, 1), round(bot.y, 1)],
            "vel": [round(bot.vx, 1), round(bot.vy, 1)],
            "hp": bot.hp,
            "ready": bot.ready_to_fire(),
        },
        "enemy": {
            "pos": [round(human.x, 1), round(human.y, 1)],
            "vel": [round(human.vx, 1), round(human.vy, 1)],
            "predicted_pos": [round(pred_x, 1), round(pred_y, 1)],
            "hp": human.hp,
        },
        "to_enemy": [round(to_x / dist, 2), round(to_y / dist, 2)],
        "dist": round(dist, 1),
        "threats": threats,
        # Split: borders are open edges; cover is hideable interior geometry
        "borders": [round(d, 1) for d in borders],
        "cover": [round(d, 1) for d in cover],
        "sit": {
            "band": band,
            "threat_count": len(threats),
            "hp_delta": bot.hp - human.hp,
            "border_pin": border_pin,
            "near_cover": near_cover,
            "enemy_speed": round(ev_spd, 1),
            "bot_ready": bot.ready_to_fire(),
        },
    }


def _seed_bridge(bridge: Bridge, layout: ArenaLayout) -> None:
    bridge.publish_snap(
        {
            "bot": {
                "pos": [layout.bot_spawn[0], layout.bot_spawn[1]],
                "vel": [0, 0],
                "hp": 100,
                "ready": True,
            },
            "enemy": {
                "pos": [layout.human_spawn[0], layout.human_spawn[1]],
                "vel": [0, 0],
                "predicted_pos": [layout.human_spawn[0], layout.human_spawn[1]],
                "hp": 100,
            },
            "threats": [],
            "borders": [300.0, 140.0, 300.0, 660.0],
            "cover": [999.0, 999.0, 999.0, 999.0],
            "to_enemy": [-1.0, 0.0],
            "dist": 520.0,
            "sit": {
                "band": "far",
                "threat_count": 0,
                "hp_delta": 0,
                "border_pin": [],
                "near_cover": [],
                "enemy_speed": 0.0,
                "bot_ready": True,
            },
        }
    )


def _present(
    screen: pygame.Surface,
    arena: pygame.Surface,
    metrics: MetricsWindow | None = None,
) -> None:
    """Arena-only main window; metrics live in a separate window."""
    screen.blit(arena, (0, 0))
    pygame.display.flip()
    if metrics is not None:
        metrics.draw(METRICS.snapshot())


def _intro_curtain(
    window: pygame.Surface,
    arena: pygame.Surface,
    layout: ArenaLayout,
    seconds: float,
    metrics: MetricsWindow | None = None,
) -> None:
    title_f = pygame.font.SysFont("georgia", 40, bold=True)
    start = pygame.time.get_ticks() / 1000.0
    while (pygame.time.get_ticks() / 1000.0) - start < seconds:
        for event in pygame.event.get():
            if event.type in (pygame.QUIT, pygame.KEYDOWN):
                return
        elapsed = (pygame.time.get_ticks() / 1000.0) - start
        arena.fill(layout.floor)
        for x, y, w, h in layout.blocks:
            pygame.draw.rect(arena, layout.steel, (x, y, w, h))
            pygame.draw.rect(arena, layout.rim, (x, y, w, h), 2)
        pulse = 0.5 + 0.5 * math.sin(elapsed * 6)
        pygame.draw.circle(arena, PALETTE["human"], layout.human_spawn, int(10 + 4 * pulse))
        pygame.draw.circle(arena, PALETTE["bot"], layout.bot_spawn, int(10 + 4 * pulse))
        title = title_f.render(layout.label, True, PALETTE["accent"])
        arena.blit(title, title.get_rect(center=(SCREEN_W // 2, 40)))
        _present(window, arena, metrics)
        pygame.time.wait(16)


def _await_game_over_choice(
    screen: pygame.Surface,
    winner: str,
    arena: pygame.Surface,
    metrics: MetricsWindow | None = None,
) -> str:
    """Show game-over until user continues or quits. Returns 'menu' or 'quit'."""
    deadline = pygame.time.get_ticks() + 3500
    while True:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return "quit"
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    return "quit"
                if event.key in (pygame.K_RETURN, pygame.K_SPACE, pygame.K_r):
                    return "menu"
        show_game_over(arena, winner, status="Returning to menu…")
        _present(screen, arena, metrics)
        if pygame.time.get_ticks() >= deadline:
            return "menu"
        pygame.time.wait(16)


def play_round(
    screen: pygame.Surface,
    bridge: Bridge,
    stop_flag: threading.Event,
    pause_flag: threading.Event,
    profile: str,
    difficulty: Difficulty,
    layout: ArenaLayout,
    metrics: MetricsWindow | None = None,
) -> str:
    """
    Run one match. Returns:
      'menu' — round finished, go back to selection
      'quit' — user closed the window / ESC
    """
    walls = barriers_from_layout(layout)
    _seed_bridge(bridge, layout)
    bridge.clear_notes()
    bridge.latest_note = ""
    METRICS.reset()
    METRICS.configure_match(
        arena_label=layout.label,
        difficulty_label=difficulty.label,
        move_speed=BOT_SPEED,
        fire_cooldown_s=BOT_COOLDOWN,
    )

    def on_note(text: str) -> None:
        bridge.latest_note = text
        METRICS.set_last_note(text)

    pause_flag.clear()
    worker = PlannerWorker(bridge, stop_flag, pause_flag, on_note, profile, difficulty)
    worker.start()
    print(f"[Match] planner live — {layout.label} / {difficulty.label}")

    # Dedicated arena surface — metrics live in a separate window
    arena = pygame.Surface((SCREEN_W, SCREEN_H))
    human = Fighter(x=layout.human_spawn[0], y=layout.human_spawn[1])
    bot = Fighter(x=layout.bot_spawn[0], y=layout.bot_spawn[1])
    shots: list[Projectile] = []
    sparks: list[Spark] = []
    flash = 0.0
    telemetry = 0.0
    body = AgentBody(x=bot.x, y=bot.y)
    over = False
    winner = ""
    memory_saved = False
    note_feed: list[str] = []
    map_idx = ARENAS.index(layout) if layout in ARENAS else 0
    current_layout = layout
    agent_mode = "LLM"
    game_paused = False
    METRICS.set_agent_mode(agent_mode)
    METRICS.set_game_paused(False)

    def _sync_planner_pause() -> None:
        """Planner runs only while playing in LLM mode."""
        if game_paused or agent_mode == "LOCAL":
            pause_flag.set()
        else:
            pause_flag.clear()

    def reset(next_layout: Optional[ArenaLayout] = None) -> None:
        nonlocal human, bot, shots, sparks, flash, telemetry, body, over, winner
        nonlocal walls, current_layout, memory_saved, note_feed, game_paused
        if next_layout is not None:
            current_layout = next_layout
            walls = barriers_from_layout(current_layout)
        human = Fighter(x=current_layout.human_spawn[0], y=current_layout.human_spawn[1])
        bot = Fighter(x=current_layout.bot_spawn[0], y=current_layout.bot_spawn[1])
        shots, sparks = [], []
        flash = telemetry = 0.0
        body = AgentBody(x=bot.x, y=bot.y)
        over = False
        winner = ""
        memory_saved = False
        note_feed = []
        game_paused = False
        METRICS.set_game_paused(False)
        _sync_planner_pause()
        bridge.clear_notes()
        bridge.latest_note = ""
        _seed_bridge(bridge, current_layout)

    _intro_curtain(screen, arena, current_layout, 1.4, metrics)
    outcome = "menu"
    clock = pygame.time.Clock()
    ai_dx = ai_dy = 0.0
    ai_fire = True
    intent_age = 999.0
    has_intent = False

    while not stop_flag.is_set():
        dt = clock.tick(FPS) / 1000.0

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                outcome = "quit"
                stop_flag.set()
                break
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    outcome = "quit"
                    stop_flag.set()
                    break
                if over and event.key in (pygame.K_r, pygame.K_RETURN, pygame.K_SPACE):
                    pass
                if not over and event.key == pygame.K_p:
                    game_paused = not game_paused
                    METRICS.set_game_paused(game_paused)
                    _sync_planner_pause()
                    if game_paused:
                        human.vx = human.vy = 0.0
                        bot.vx = bot.vy = 0.0
                        print(f"[Match] paused — mode {agent_mode}")
                    else:
                        print(f"[Match] resumed — mode {agent_mode}")
                if not over and game_paused and event.key == pygame.K_TAB:
                    agent_mode = "LOCAL" if agent_mode == "LLM" else "LLM"
                    METRICS.set_agent_mode(agent_mode)
                    _sync_planner_pause()
                    print(f"[Match] ablation mode → {agent_mode}")
                if not over and not game_paused and event.key == pygame.K_1 and map_idx != 0:
                    map_idx = 0
                    reset(ARENAS[0])
                    _intro_curtain(screen, arena, ARENAS[0], 1.0, metrics)
                elif not over and not game_paused and event.key == pygame.K_2 and map_idx != 1:
                    map_idx = 1
                    reset(ARENAS[1])
                    _intro_curtain(screen, arena, ARENAS[1], 1.0, metrics)
                elif not over and not game_paused and event.key == pygame.K_3 and map_idx != 2:
                    map_idx = 2
                    reset(ARENAS[2])
                    _intro_curtain(screen, arena, ARENAS[2], 1.0, metrics)

        if stop_flag.is_set():
            break

        if over:
            if not memory_saved:
                pause_flag.set()
                show_game_over(arena, winner, status="Writing long-term memory…")
                _present(screen, arena, metrics)
                persist_match(bridge.dump_notes(), winner)
                memory_saved = True
            outcome = _await_game_over_choice(screen, winner, arena, metrics)
            break

        mx, my = pygame.mouse.get_pos()
        mx = max(0, min(SCREEN_W - 1, mx))
        my = max(0, min(SCREEN_H - 1, my))

        if not game_paused:
            keys = pygame.key.get_pressed()
            pdx = pdy = 0.0
            if keys[pygame.K_w] or keys[pygame.K_UP]:
                pdy -= 1
            if keys[pygame.K_s] or keys[pygame.K_DOWN]:
                pdy += 1
            if keys[pygame.K_a] or keys[pygame.K_LEFT]:
                pdx -= 1
            if keys[pygame.K_d] or keys[pygame.K_RIGHT]:
                pdx += 1
            mag = math.hypot(pdx, pdy)
            if mag > 0:
                pdx, pdy = pdx / mag, pdy / mag
            human.vx = pdx * PLAYER_SPEED
            human.vy = pdy * PLAYER_SPEED

            if (pygame.mouse.get_pressed()[0] or keys[pygame.K_SPACE]) and human.ready_to_fire():
                shot = human.shoot_at(mx, my, "player")
                if shot:
                    shots.append(shot)
                    METRICS.record_player_shot()
                    sparks += muzzle_sparks(
                        human.x, human.y, shot.vx, shot.vy, PALETTE["shot_human"]
                    )

            if agent_mode == "LLM":
                ai_dx, ai_dy, ai_fire, intent_age, has_intent = bridge.read_intent()
            else:
                # LOCAL: ignore stale LLM packets; chase/dodge only
                ai_dx, ai_dy, ai_fire, intent_age, has_intent = 0.0, 0.0, True, 999.0, False

            body.x, body.y = bot.x, bot.y
            body.vx, body.vy = bot.vx, bot.vy
            body.plan_dx, body.plan_dy = ai_dx, ai_dy
            body.plan_fire = True if agent_mode == "LOCAL" else ai_fire

            threats = [
                ThreatShot(s.x, s.y, s.vx, s.vy, s.owner)
                for s in shots
                if s.owner == "player"
            ]
            lead_x = human.x + human.vx * difficulty.lead_time
            lead_y = human.y + human.vy * difficulty.lead_time
            rvx, rvy, rfire, used_llm, under_threat, dodging = hunt_and_fire(
                body,
                HuntTarget(
                    x=human.x,
                    y=human.y,
                    vx=human.vx,
                    vy=human.vy,
                    lead_x=lead_x,
                    lead_y=lead_y,
                ),
                threats,
                walls,
                difficulty.bot_speed,
                SCREEN_W,
                SCREEN_H,
                intent_age_s=intent_age,
                has_intent=has_intent and agent_mode == "LLM",
            )
            bot.vx, bot.vy = rvx, rvy
            METRICS.record_control_frame(
                used_llm=used_llm and agent_mode == "LLM",
                under_threat=under_threat,
                dodging=dodging,
            )

            if rfire and bot.ready_to_fire():
                shot = bot.shoot_at(
                    lead_x, lead_y, "bot", cooldown=difficulty.bot_cooldown
                )
                if shot:
                    shots.append(shot)
                    METRICS.record_bot_shot()
                    sparks += muzzle_sparks(
                        bot.x, bot.y, shot.vx, shot.vy, PALETTE["shot_bot"]
                    )

            human.x += human.vx * dt
            human.y += human.vy * dt
            bot.x += bot.vx * dt
            bot.y += bot.vy * dt
            push_out_of_walls(human, walls)
            push_out_of_walls(bot, walls)
            keep_inside_arena(human)
            keep_inside_arena(bot)
            human.cool -= dt
            bot.cool -= dt

            for shot in shots:
                shot.x += shot.vx * dt
                shot.y += shot.vy * dt
                shot.age += dt

            kept: list[Projectile] = []
            for shot in shots:
                if not shot.alive:
                    continue
                if projectile_blocked(shot, walls):
                    sparks += burst_sparks(shot.x, shot.y, (140, 120, 90), 4)
                    continue
                hit = False
                if shot.owner == "player" and bot.alive:
                    if math.hypot(shot.x - bot.x, shot.y - bot.y) < 21:
                        bot.take_hit()
                        METRICS.record_player_hit()
                        sparks += burst_sparks(shot.x, shot.y, PALETTE["bot"], 10)
                        hit = True
                if shot.owner == "bot" and human.alive:
                    if math.hypot(shot.x - human.x, shot.y - human.y) < 21:
                        human.take_hit()
                        METRICS.record_bot_hit()
                        flash = 0.35
                        sparks += burst_sparks(shot.x, shot.y, PALETTE["human"], 10)
                        hit = True
                if not hit:
                    kept.append(shot)
            shots = kept

            for spark in sparks:
                spark.x += spark.vx * dt
                spark.y += spark.vy * dt
                spark.life -= dt
            sparks = [s for s in sparks if s.alive]
            flash = max(0.0, flash - dt)

            if not human.alive and not over:
                winner, over = "bot", True
            elif not bot.alive and not over:
                winner, over = "player", True

            telemetry += dt
            if telemetry >= TELEMETRY_INTERVAL:
                telemetry = 0.0
                bridge.publish_snap(
                    compose_snapshot(human, bot, shots, walls, difficulty)
                )
        else:
            # Frozen: keep last intent readout for HUD; no sim ticks
            if agent_mode == "LLM":
                ai_dx, ai_dy, ai_fire, intent_age, has_intent = bridge.read_intent()
            else:
                ai_dx, ai_dy, ai_fire, intent_age, has_intent = 0.0, 0.0, True, 999.0, False

        blit_floor(arena, current_layout)
        paint_barriers(arena, walls, current_layout)
        paint_sparks(arena, sparks)
        for shot in shots:
            paint_shot(arena, shot)
        paint_fighter(arena, human, PALETTE["human"], PALETTE["human_shade"], "YOU")
        paint_fighter(arena, bot, PALETTE["bot"], PALETTE["bot_shade"], "ECHO")
        if human.alive and not game_paused:
            paint_aim(arena, human.x, human.y, mx, my)
        pygame.draw.rect(arena, current_layout.rim, (0, 0, SCREEN_W, SCREEN_H), 3)

        note = bridge.latest_note
        # Accept each new planner note even if wording repeats later
        if note and (not note_feed or note_feed[-1] != note):
            note_feed.append(note)
            bridge.push_note(note)
            note_feed = note_feed[-12:]

        paint_hud(
            arena,
            human,
            bot,
            (ai_dx, ai_dy, ai_fire),
            note_feed,
            clock.get_fps(),
            flash,
            current_layout,
            agent_mode=agent_mode,
            game_paused=game_paused,
        )

        fps_now = clock.get_fps()
        METRICS.set_fps(fps_now)
        METRICS.set_intent(
            age_s=intent_age if has_intent and agent_mode == "LLM" else 999.0,
            mx=ai_dx,
            my=ai_dy,
            fire=ai_fire,
            has_intent=has_intent and agent_mode == "LLM",
        )
        METRICS.set_combat_state(
            player_hp=human.hp,
            bot_hp=bot.hp,
            active_projectiles=len(shots),
            player_alive=human.alive,
            bot_alive=bot.alive,
            distance_px=math.hypot(human.x - bot.x, human.y - bot.y),
        )

        _present(screen, arena, metrics)

    stop_flag.set()
    worker.join(timeout=4.0)
    print("[Match] round closed.")
    return outcome


def main() -> None:
    pygame.init()
    pygame.font.init()
    warm_caches()
    screen = pygame.display.set_mode((SCREEN_W, SCREEN_H))
    pygame.display.set_caption(WINDOW_TITLE)
    metrics = MetricsWindow()

    running = True
    try:
        while running:
            profile = load_profile()
            if profile:
                print(f"[EchoArena] loaded profile ({len(profile)} chars)")
            else:
                print("[EchoArena] no prior profile — fresh slate")

            metrics.draw(METRICS.snapshot())

            diff_idx = pick_difficulty(screen)
            if diff_idx is None:
                break

            map_idx = pick_arena(screen)
            if map_idx is None:
                break

            difficulty = DIFFICULTIES[diff_idx]
            layout = ARENAS[map_idx]
            bridge = Bridge()
            stop_flag = threading.Event()
            pause_flag = threading.Event()

            outcome = play_round(
                screen,
                bridge,
                stop_flag,
                pause_flag,
                profile,
                difficulty,
                layout,
                metrics,
            )
            if outcome == "quit":
                running = False
            else:
                print("[EchoArena] back to menu — memory retained for next match.")
    finally:
        metrics.destroy()
        pygame.quit()
    print("[EchoArena] done.")


if __name__ == "__main__":
    main()
