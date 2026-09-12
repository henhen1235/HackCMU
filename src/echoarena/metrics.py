"""Thread-safe optimization metrics — comprehensive live telemetry."""

from __future__ import annotations

import statistics
import threading
import time
from collections import deque
from dataclasses import dataclass


def _percentile(ordered: list[float], p: float) -> float:
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return ordered[0]
    idx = min(len(ordered) - 1, max(0, int(round((p / 100.0) * (len(ordered) - 1)))))
    return ordered[idx]


@dataclass(frozen=True)
class FlightSlot:
    instance: int
    step: int
    age_s: float


@dataclass(frozen=True)
class MetricsSnapshot:
    # Frame / loop
    fps: float
    fps_min: float
    fps_avg: float
    fps_max: float
    frame_ms: float
    match_elapsed_s: float

    # Planner latency
    latency_last_ms: float
    latency_min_ms: float
    latency_max_ms: float
    latency_mean_ms: float
    latency_p50_ms: float
    latency_p95_ms: float
    latency_p99_ms: float
    latency_samples: int

    # Pipeline concurrency
    in_flight: int
    in_flight_peak: int
    planner_launches: int
    planner_interval_s: float
    planner_max_in_flight: int
    active_flights: tuple[FlightSlot, ...]
    last_instance: int
    last_step: int
    last_outcome: str
    last_return_age_s: float

    # Outcomes
    ok: int
    parse_fail: int
    http_error: int
    rate_limited: int
    stale: int
    total_planner_results: int
    success_rate_pct: float
    stale_rate_pct: float
    parse_fail_rate_pct: float
    rate_limit_rate_pct: float

    # Intent
    intent_age_s: float
    intent_mx: float
    intent_my: float
    intent_fire: bool
    has_intent: bool
    intent_accepts: int
    last_ok_age_s: float
    last_note: str

    # LLM influence
    llm_influence_frames: int
    local_only_frames: int
    llm_influence_pct: float
    threat_frames: int
    dodge_frames: int

    # Combat
    player_hp: int
    bot_hp: int
    player_shots: int
    bot_shots: int
    player_hits: int
    bot_hits: int
    player_accuracy_pct: float
    bot_accuracy_pct: float
    active_projectiles: int
    player_alive: bool
    bot_alive: bool
    distance_px: float

    # Config / identity
    arena_label: str
    difficulty_label: str
    model_name: str
    move_speed: float
    fire_cooldown_s: float
    agent_mode: str
    game_paused: bool


class MetricsRegistry:
    """Collect every useful planner/game signal for the metrics dashboard."""

    def __init__(self, latency_window: int = 120, fps_window: int = 180) -> None:
        self._lock = threading.Lock()
        self._match_start = time.monotonic()

        self._fps = 0.0
        self._fps_samples: deque[float] = deque(maxlen=fps_window)
        self._frame_ms = 0.0

        self._latency_last_ms = 0.0
        self._latencies_ms: deque[float] = deque(maxlen=latency_window)

        self._in_flight = 0
        self._in_flight_peak = 0
        self._planner_launches = 0
        self._planner_interval_s = 1.0
        self._planner_max_in_flight = 2
        # instance -> (step, started_monotonic)
        self._active: dict[int, tuple[int, float]] = {}
        self._last_instance = -1
        self._last_step = -1
        self._last_outcome = "—"
        self._last_return_at = 0.0

        self._ok = 0
        self._parse_fail = 0
        self._http_error = 0
        self._rate_limited = 0
        self._stale = 0

        self._intent_age_s = 999.0
        self._intent_mx = 0.0
        self._intent_my = 0.0
        self._intent_fire = False
        self._has_intent = False
        self._intent_accepts = 0
        self._last_ok_at = 0.0
        self._last_note = ""

        self._llm_influence_frames = 0
        self._local_only_frames = 0
        self._threat_frames = 0
        self._dodge_frames = 0

        self._player_hp = 100
        self._bot_hp = 100
        self._player_shots = 0
        self._bot_shots = 0
        self._player_hits = 0
        self._bot_hits = 0
        self._active_projectiles = 0
        self._player_alive = True
        self._bot_alive = True
        self._distance_px = 0.0

        self._arena_label = "—"
        self._difficulty_label = "—"
        self._model_name = "—"
        self._move_speed = 200.0
        self._fire_cooldown_s = 0.22
        self._agent_mode = "LLM"
        self._game_paused = False

    def reset(self) -> None:
        with self._lock:
            self._match_start = time.monotonic()
            self._fps = 0.0
            self._fps_samples.clear()
            self._frame_ms = 0.0
            self._latency_last_ms = 0.0
            self._latencies_ms.clear()
            self._in_flight = 0
            self._in_flight_peak = 0
            self._planner_launches = 0
            self._active.clear()
            self._last_instance = -1
            self._last_step = -1
            self._last_outcome = "—"
            self._last_return_at = 0.0
            self._ok = 0
            self._parse_fail = 0
            self._http_error = 0
            self._rate_limited = 0
            self._stale = 0
            self._intent_age_s = 999.0
            self._intent_mx = 0.0
            self._intent_my = 0.0
            self._intent_fire = False
            self._has_intent = False
            self._intent_accepts = 0
            self._last_ok_at = 0.0
            self._last_note = ""
            self._llm_influence_frames = 0
            self._local_only_frames = 0
            self._threat_frames = 0
            self._dodge_frames = 0
            self._player_hp = 100
            self._bot_hp = 100
            self._player_shots = 0
            self._bot_shots = 0
            self._player_hits = 0
            self._bot_hits = 0
            self._active_projectiles = 0
            self._player_alive = True
            self._bot_alive = True
            self._distance_px = 0.0
            self._agent_mode = "LLM"
            self._game_paused = False

    def configure_planner(
        self,
        *,
        interval_s: float,
        max_in_flight: int,
        model_name: str,
    ) -> None:
        with self._lock:
            self._planner_interval_s = float(interval_s)
            self._planner_max_in_flight = int(max_in_flight)
            self._model_name = model_name

    def configure_match(
        self,
        *,
        arena_label: str,
        difficulty_label: str,
        move_speed: float,
        fire_cooldown_s: float,
    ) -> None:
        with self._lock:
            self._arena_label = arena_label
            self._difficulty_label = difficulty_label
            self._move_speed = float(move_speed)
            self._fire_cooldown_s = float(fire_cooldown_s)
            self._match_start = time.monotonic()

    def set_agent_mode(self, mode: str) -> None:
        with self._lock:
            self._agent_mode = mode if mode in ("LOCAL", "LLM") else "LLM"

    def set_game_paused(self, paused: bool) -> None:
        with self._lock:
            self._game_paused = bool(paused)

    def set_fps(self, fps: float) -> None:
        fps = max(0.0, float(fps))
        with self._lock:
            self._fps = fps
            if fps > 0:
                self._fps_samples.append(fps)
                self._frame_ms = 1000.0 / fps

    def set_intent(
        self,
        *,
        age_s: float,
        mx: float,
        my: float,
        fire: bool,
        has_intent: bool,
    ) -> None:
        with self._lock:
            self._intent_age_s = max(0.0, float(age_s))
            self._intent_mx = float(mx)
            self._intent_my = float(my)
            self._intent_fire = bool(fire)
            self._has_intent = bool(has_intent)

    def set_in_flight(self, n: int) -> None:
        n = max(0, int(n))
        with self._lock:
            self._in_flight = n
            self._in_flight_peak = max(self._in_flight_peak, n)
            if n == 0:
                self._active.clear()

    def flight_launch(self, *, instance: int, step: int) -> None:
        with self._lock:
            self._planner_launches += 1
            self._active[int(instance)] = (int(step), time.monotonic())
            self._in_flight = len(self._active)
            self._in_flight_peak = max(self._in_flight_peak, self._in_flight)

    def flight_finish(
        self,
        *,
        instance: int,
        step: int,
        outcome: str,
        latency_s: float,
    ) -> None:
        ms = max(0.0, float(latency_s) * 1000.0)
        with self._lock:
            self._active.pop(int(instance), None)
            self._in_flight = len(self._active)
            self._last_instance = int(instance)
            self._last_step = int(step)
            self._last_outcome = outcome
            self._last_return_at = time.monotonic()
            self._latency_last_ms = ms
            self._latencies_ms.append(ms)
            if outcome == "ok":
                self._ok += 1
                self._intent_accepts += 1
                self._last_ok_at = self._last_return_at
            elif outcome == "parse_fail":
                self._parse_fail += 1
            elif outcome == "stale":
                self._stale += 1
            elif outcome == "rate_limited":
                self._http_error += 1
                self._rate_limited += 1
            else:
                self._http_error += 1

    def set_last_note(self, note: str) -> None:
        with self._lock:
            self._last_note = (note or "")[:120]

    def record_control_frame(
        self,
        *,
        used_llm: bool,
        under_threat: bool,
        dodging: bool,
    ) -> None:
        with self._lock:
            if used_llm:
                self._llm_influence_frames += 1
            else:
                self._local_only_frames += 1
            if under_threat:
                self._threat_frames += 1
            if dodging:
                self._dodge_frames += 1

    def record_player_shot(self) -> None:
        with self._lock:
            self._player_shots += 1

    def record_bot_shot(self) -> None:
        with self._lock:
            self._bot_shots += 1

    def record_player_hit(self) -> None:
        with self._lock:
            self._player_hits += 1

    def record_bot_hit(self) -> None:
        with self._lock:
            self._bot_hits += 1

    def set_combat_state(
        self,
        *,
        player_hp: int,
        bot_hp: int,
        active_projectiles: int,
        player_alive: bool,
        bot_alive: bool,
        distance_px: float,
    ) -> None:
        with self._lock:
            self._player_hp = int(player_hp)
            self._bot_hp = int(bot_hp)
            self._active_projectiles = int(active_projectiles)
            self._player_alive = bool(player_alive)
            self._bot_alive = bool(bot_alive)
            self._distance_px = float(distance_px)

    def _latency_stats_locked(self) -> tuple[float, float, float, float, float, float, int]:
        vals = list(self._latencies_ms)
        if not vals:
            return 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0
        ordered = sorted(vals)
        mean = statistics.fmean(ordered)
        return (
            ordered[0],
            ordered[-1],
            mean,
            _percentile(ordered, 50),
            _percentile(ordered, 95),
            _percentile(ordered, 99),
            len(ordered),
        )

    def _fps_stats_locked(self) -> tuple[float, float, float]:
        vals = list(self._fps_samples)
        if not vals:
            return 0.0, 0.0, 0.0
        return min(vals), statistics.fmean(vals), max(vals)

    def snapshot(self) -> MetricsSnapshot:
        with self._lock:
            amin, amax, mean, p50, p95, p99, nlat = self._latency_stats_locked()
            fmin, favg, fmax = self._fps_stats_locked()
            total = self._ok + self._parse_fail + self._http_error + self._stale
            decided = self._ok + self._parse_fail + self._http_error
            control_frames = self._llm_influence_frames + self._local_only_frames
            now = time.monotonic()
            last_ok_age = (now - self._last_ok_at) if self._last_ok_at else 999.0
            last_ret_age = (now - self._last_return_at) if self._last_return_at else 999.0
            flights = tuple(
                FlightSlot(
                    instance=inst,
                    step=step,
                    age_s=max(0.0, now - started),
                )
                for inst, (step, started) in sorted(self._active.items())
            )
            return MetricsSnapshot(
                fps=self._fps,
                fps_min=fmin,
                fps_avg=favg,
                fps_max=fmax,
                frame_ms=self._frame_ms,
                match_elapsed_s=now - self._match_start,
                latency_last_ms=self._latency_last_ms,
                latency_min_ms=amin,
                latency_max_ms=amax,
                latency_mean_ms=mean,
                latency_p50_ms=p50,
                latency_p95_ms=p95,
                latency_p99_ms=p99,
                latency_samples=nlat,
                in_flight=self._in_flight,
                in_flight_peak=self._in_flight_peak,
                planner_launches=self._planner_launches,
                planner_interval_s=self._planner_interval_s,
                planner_max_in_flight=self._planner_max_in_flight,
                active_flights=flights,
                last_instance=self._last_instance,
                last_step=self._last_step,
                last_outcome=self._last_outcome,
                last_return_age_s=last_ret_age,
                ok=self._ok,
                parse_fail=self._parse_fail,
                http_error=self._http_error,
                rate_limited=self._rate_limited,
                stale=self._stale,
                total_planner_results=total,
                success_rate_pct=(100.0 * self._ok / decided) if decided else 0.0,
                stale_rate_pct=(100.0 * self._stale / total) if total else 0.0,
                parse_fail_rate_pct=(100.0 * self._parse_fail / decided) if decided else 0.0,
                rate_limit_rate_pct=(100.0 * self._rate_limited / decided) if decided else 0.0,
                intent_age_s=self._intent_age_s,
                intent_mx=self._intent_mx,
                intent_my=self._intent_my,
                intent_fire=self._intent_fire,
                has_intent=self._has_intent,
                intent_accepts=self._intent_accepts,
                last_ok_age_s=last_ok_age,
                last_note=self._last_note,
                llm_influence_frames=self._llm_influence_frames,
                local_only_frames=self._local_only_frames,
                llm_influence_pct=(
                    100.0 * self._llm_influence_frames / control_frames
                    if control_frames
                    else 0.0
                ),
                threat_frames=self._threat_frames,
                dodge_frames=self._dodge_frames,
                player_hp=self._player_hp,
                bot_hp=self._bot_hp,
                player_shots=self._player_shots,
                bot_shots=self._bot_shots,
                player_hits=self._player_hits,
                bot_hits=self._bot_hits,
                player_accuracy_pct=(
                    100.0 * self._player_hits / self._player_shots
                    if self._player_shots
                    else 0.0
                ),
                bot_accuracy_pct=(
                    100.0 * self._bot_hits / self._bot_shots if self._bot_shots else 0.0
                ),
                active_projectiles=self._active_projectiles,
                player_alive=self._player_alive,
                bot_alive=self._bot_alive,
                distance_px=self._distance_px,
                arena_label=self._arena_label,
                difficulty_label=self._difficulty_label,
                model_name=self._model_name,
                move_speed=self._move_speed,
                fire_cooldown_s=self._fire_cooldown_s,
                agent_mode=self._agent_mode,
                game_paused=self._game_paused,
            )


METRICS = MetricsRegistry()
