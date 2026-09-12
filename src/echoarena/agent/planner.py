"""Pipelined xAI/Grok planner — strategy only, never blocks the game loop."""

from __future__ import annotations

import asyncio
import json
import re
import threading
import time
from typing import Callable

from echoarena.agent.ifm_client import IFM_MODEL, IFMError, chat_completion
from echoarena.agent.memory import wrap_profile_for_prompt
from echoarena.agent.prompts import (
    fill_combat_prompt,
    playbook_note,
    suggest_tactic,
    situation_from_snap,
)
from echoarena.metrics import METRICS

_NOTE_RE = re.compile(
    r"<(?:note|thinking)>\s*(.*?)\s*</(?:note|thinking)>",
    re.DOTALL | re.IGNORECASE,
)
_THINK_LINE_RE = re.compile(
    r"(?:^|\n)\s*(?:thinking|tactic|plan)\s*[:\-–]\s*(.+?)(?:\n|$)",
    re.IGNORECASE,
)
_JSON_RE = re.compile(r"\{[^{}]*\}", re.DOTALL)
_FLUFF_RE = re.compile(
    r"\b("
    r"flank\s+(?:hard\s+)?(?:left|right)"
    r"|feint\b"
    r"|press\s+predicted"
    r"|strafe(?:s|ing)?\s+threats"
    r")\b",
    re.IGNORECASE,
)


def _tactic_from_intent(mx: float, my: float, fire: bool, snap: dict | None = None) -> str:
    """Readable fallback when the model skips <thinking> tags."""
    if snap is not None:
        return playbook_note(snap, mx, my, fire)
    ax, ay = abs(mx), abs(my)
    if ax < 0.2 and ay < 0.2:
        move = "hold / micro-adjust"
    elif ax >= ay:
        move = "press right" if mx > 0 else "press left"
        if ay > 0.35:
            move += " and up" if my > 0 else " and down"
    else:
        move = "press down" if my > 0 else "press up"
        if ax > 0.35:
            move += " and right" if mx > 0 else " and left"
    shoot = "keep firing" if fire else "hold fire"
    return f"{move}, {shoot}"


def _note_is_weak(note: str | None) -> bool:
    if not note or len(note.strip()) < 8:
        return True
    return bool(_FLUFF_RE.search(note))


def _parse_intent(text: str) -> tuple[dict | None, str | None]:
    note = None
    m = _NOTE_RE.search(text)
    if m:
        note = m.group(1).strip()
    if not note:
        m2 = _THINK_LINE_RE.search(text)
        if m2:
            note = m2.group(1).strip().strip('"').strip("'")

    cleaned = _NOTE_RE.sub("", text).strip()
    cleaned = re.sub(r",\s*([}\]])", r"\1", cleaned)

    # Prefer the last JSON object that looks like an intent
    for match in reversed(list(_JSON_RE.finditer(cleaned))):
        try:
            payload = json.loads(match.group())
        except json.JSONDecodeError:
            continue
        mx = payload.get("dx", payload.get("mx"))
        my = payload.get("dy", payload.get("my"))
        fire = payload.get("shoot", payload.get("fire"))
        if mx is None and my is None and fire is None:
            continue
        intent = {
            "mx": float(mx if mx is not None else 0.0),
            "my": float(my if my is not None else 0.0),
            "fire": bool(fire) if fire is not None else True,
            "tick_id": int(payload.get("tick_id", -1)),
        }
        if not note:
            note = _tactic_from_intent(intent["mx"], intent["my"], intent["fire"])
        return intent, note

    mx_m = re.search(r'"(?:dx|mx)"\s*:\s*([+-]?\d+\.?\d*)', cleaned)
    my_m = re.search(r'"(?:dy|my)"\s*:\s*([+-]?\d+\.?\d*)', cleaned)
    fire_m = re.search(r'"(?:shoot|fire)"\s*:\s*(true|false)', cleaned, re.I)
    if mx_m or my_m or fire_m:
        intent = {
            "mx": float(mx_m.group(1)) if mx_m else 0.0,
            "my": float(my_m.group(1)) if my_m else 0.0,
            "fire": (fire_m.group(1).lower() == "true") if fire_m else True,
            "tick_id": -1,
        }
        if not note:
            note = _tactic_from_intent(intent["mx"], intent["my"], intent["fire"])
        return intent, note

    return None, note


async def run_planner(
    get_snapshot: Callable[[], dict],
    on_intent: Callable[[dict], None],
    *,
    map_width: int = 800,
    map_height: int = 600,
    latency_ms: int = 300,
    min_wall_distance: int = 50,
    workers: int = 1,
    interval: float = 1.5,
    max_in_flight: int = 1,
    model: str | None = None,
    max_tokens: int = 300,
    timeout_s: float = 6.0,
    backoff_429_s: float = 4.0,
    stop_event: asyncio.Event | None = None,
    pause_event: threading.Event | None = None,
    on_note: Callable[[str], None] | None = None,
    player_memory: str = "",
) -> None:
    """Fire planner calls at a sustainable rate; hard-backoff on HTTP 429."""
    del workers
    profile_block = wrap_profile_for_prompt(player_memory)
    latest_accepted = -1
    accept_lock = asyncio.Lock()
    model_name = model or IFM_MODEL
    cooldown_until = 0.0
    consecutive_429 = 0
    last_cmd: dict | None = None
    last_note: str | None = None
    latch_streak = 0
    METRICS.configure_planner(
        interval_s=interval,
        max_in_flight=max_in_flight,
        model_name=model_name,
    )

    async def _one_call(tick: int, instance: int) -> None:
        nonlocal latest_accepted, cooldown_until, consecutive_429
        nonlocal last_cmd, last_note, latch_streak
        if pause_event is not None and pause_event.is_set():
            return
        snap = get_snapshot()
        # Never echo prior thinking as Style — that caused note/intent latch loops.
        if "sit" not in snap:
            snap = {**snap, "sit": situation_from_snap(snap)}
        hint = suggest_tactic(snap["sit"], tick)
        anti: dict = {
            "tick_id": tick,
            "instance": instance,
            "prefer_tactic": hint,
        }
        if last_cmd is not None:
            anti["last_cmd"] = last_cmd
        if last_note:
            anti["last_note"] = last_note
            anti["do_not_repeat"] = (
                "Change TACTIC name or WHY fact. borders=map edges (not cover); "
                "cover=interior blocks only. Reverse off a border only if <20."
            )
        snap = {**snap, **anti}
        # Compact JSON = fewer prompt tokens → keeps planner latency down
        prompt = fill_combat_prompt(
            game_state_json=json.dumps(snap, separators=(",", ":")),
            map_width=map_width,
            map_height=map_height,
            latency_ms=latency_ms,
            min_wall_distance=min_wall_distance,
            player_memory=profile_block,
            tick_id=tick,
        )
        started = time.perf_counter()
        print(f"[Planner inst{instance} step{tick:03d}] launch")
        METRICS.flight_launch(instance=instance, step=tick)
        try:
            raw = await chat_completion(
                [{"role": "user", "content": prompt}],
                model=model_name,
                max_tokens=max_tokens,
                temperature=0.6,
                timeout=timeout_s,
            )
        except IFMError as exc:
            msg = str(exc)
            rate_limited = (
                getattr(exc, "status_code", None) == 429
                or "429" in msg
                or "rate_limit" in msg.lower()
            )
            outcome = "rate_limited" if rate_limited else "http_error"
            METRICS.flight_finish(
                instance=instance,
                step=tick,
                outcome=outcome,
                latency_s=time.perf_counter() - started,
            )
            if rate_limited:
                consecutive_429 += 1
                wait = min(30.0, backoff_429_s * (1.6 ** min(consecutive_429 - 1, 4)))
                cooldown_until = time.monotonic() + wait
                print(
                    f"[Planner inst{instance} step{tick:03d}] 429 — cooling {wait:.1f}s "
                    f"(streak={consecutive_429})"
                )
            else:
                print(f"[Planner inst{instance} step{tick:03d}] api error: {exc}")
            return
        except Exception as exc:  # noqa: BLE001
            METRICS.flight_finish(
                instance=instance,
                step=tick,
                outcome="http_error",
                latency_s=time.perf_counter() - started,
            )
            print(f"[Planner inst{instance} step{tick:03d}] api error: {exc}")
            return

        if pause_event is not None and pause_event.is_set():
            METRICS.flight_finish(
                instance=instance,
                step=tick,
                outcome="http_error",
                latency_s=time.perf_counter() - started,
            )
            return

        consecutive_429 = 0
        cooldown_until = 0.0

        intent, note = _parse_intent(raw)
        elapsed = time.perf_counter() - started

        if not intent:
            METRICS.flight_finish(
                instance=instance, step=tick, outcome="parse_fail", latency_s=elapsed
            )
            print(f"[Planner inst{instance} step{tick:03d}] parse fail ({elapsed:.2f}s): {raw[:80]}")
            return

        mx = float(intent["mx"])
        my = float(intent["my"])
        # Holding fire made the bot lose fights — always shoot when ready.
        fire = True

        # Break vector latch: same nearly-identical packet for 2+ accepts → recompute.
        if last_cmd is not None:
            same = abs(mx - last_cmd["dx"]) + abs(my - last_cmd["dy"]) < 0.28
            latch_streak = latch_streak + 1 if same else 0
        else:
            latch_streak = 0

        # Only kill into-BORDER motion when nearly leaving the map.
        # Interior cover is hideable — do not reverse away from it here.
        borders = snap.get("borders") or snap.get("walls") or []
        to_enemy = snap.get("to_enemy") or [0.0, 0.0]
        _PIN = 20.0
        if len(borders) >= 4:
            if borders[3] < _PIN and mx < -0.25:
                mx = 0.15
            if borders[1] < _PIN and mx > 0.25:
                mx = -0.15
            if borders[0] < _PIN and my < -0.25:
                my = 0.15
            if borders[2] < _PIN and my > 0.25:
                my = -0.15

        if latch_streak >= 3:
            tx, ty = float(to_enemy[0]), float(to_enemy[1])
            sx, sy = (-ty, tx) if latch_streak % 2 else (ty, -tx)
            mx = 0.6 * tx + 0.4 * sx
            my = 0.6 * ty + 0.4 * sy
            band = (snap.get("sit") or {}).get("band", "?")
            note = (
                f"TACTIC: reset_angle | WHY: latched_cmd,band={band} "
                f"| vec=({mx:+.2f},{my:+.2f})"
            )
            latch_streak = 0

        mag = (mx * mx + my * my) ** 0.5
        if mag > 1e-6:
            mx, my = mx / mag, my / mag

        if _note_is_weak(note) or (
            note and last_note and note.strip().lower() == last_note.strip().lower()
        ):
            note = playbook_note(snap, mx, my, fire)

        if note:
            if on_note:
                on_note(note)
                METRICS.set_last_note(note)

        tick_id = intent.get("tick_id", tick)
        if tick_id < 0:
            tick_id = tick
        async with accept_lock:
            if tick_id < latest_accepted:
                METRICS.flight_finish(
                    instance=instance, step=tick_id, outcome="stale", latency_s=elapsed
                )
                print(
                    f"[Planner inst{instance} step{tick:03d}] stale discard "
                    f"(tick_id={tick_id})"
                )
                return
            latest_accepted = tick_id

        intent_out = {"dx": mx, "dy": my, "shoot": fire, "tick_id": tick_id}
        last_cmd = {"dx": round(mx, 2), "dy": round(my, 2), "shoot": fire}
        last_note = note
        on_intent(intent_out)
        METRICS.flight_finish(
            instance=instance, step=tick_id, outcome="ok", latency_s=elapsed
        )
        print(
            f"[Planner inst{instance} step{tick_id:03d}] ok ({elapsed:.2f}s) "
            f"note={note!r} -> {{'mx': {mx:.2f}, 'my': {my:.2f}, 'fire': {fire}}}"
        )

    print(
        f"Planner online — model {model_name}, interval {interval}s, "
        f"max_in_flight {max_in_flight}, timeout {timeout_s}s"
    )
    tasks: list[asyncio.Task] = []
    free_instances = list(range(max(1, max_in_flight)))
    tick = 0
    try:
        while True:
            if stop_event and stop_event.is_set():
                break
            if pause_event is not None and pause_event.is_set():
                METRICS.set_in_flight(0)
                await asyncio.sleep(0.1)
                continue

            now = time.monotonic()
            if now < cooldown_until:
                await asyncio.sleep(min(0.25, cooldown_until - now))
                continue

            # Reclaim finished tasks → free their instance ids
            still: list[asyncio.Task] = []
            for t in tasks:
                if t.done():
                    inst = getattr(t, "echo_instance", None)
                    if inst is not None and inst not in free_instances:
                        free_instances.append(inst)
                else:
                    still.append(t)
            tasks = still
            free_instances.sort()

            if not free_instances:
                await asyncio.sleep(0.05)
                continue

            instance = free_instances.pop(0)
            task = asyncio.create_task(_one_call(tick, instance))
            task.echo_instance = instance  # type: ignore[attr-defined]
            tasks.append(task)
            tick += 1
            await asyncio.sleep(interval)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        print("Planner shutting down…")
        if tasks:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        METRICS.set_in_flight(0)
        print("Planner stopped.")
