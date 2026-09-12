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
from echoarena.agent.prompts import fill_combat_prompt
from echoarena.metrics import METRICS

_NOTE_RE = re.compile(r"<(?:note|thinking)>(.*?)</(?:note|thinking)>", re.DOTALL)
_JSON_RE = re.compile(r"\{[^{}]*\}", re.DOTALL)


def _parse_intent(text: str) -> tuple[dict | None, str | None]:
    note = None
    m = _NOTE_RE.search(text)
    if m:
        note = m.group(1).strip()

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
        return {
            "mx": float(mx if mx is not None else 0.0),
            "my": float(my if my is not None else 0.0),
            "fire": bool(fire) if fire is not None else True,
            "tick_id": int(payload.get("tick_id", -1)),
        }, note

    mx_m = re.search(r'"(?:dx|mx)"\s*:\s*([+-]?\d+\.?\d*)', cleaned)
    my_m = re.search(r'"(?:dy|my)"\s*:\s*([+-]?\d+\.?\d*)', cleaned)
    fire_m = re.search(r'"(?:shoot|fire)"\s*:\s*(true|false)', cleaned, re.I)
    if mx_m or my_m or fire_m:
        return {
            "mx": float(mx_m.group(1)) if mx_m else 0.0,
            "my": float(my_m.group(1)) if my_m else 0.0,
            "fire": (fire_m.group(1).lower() == "true") if fire_m else True,
            "tick_id": -1,
        }, note

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
    session_notes: list[str] = []
    profile_block = wrap_profile_for_prompt(player_memory)
    latest_accepted = -1
    accept_lock = asyncio.Lock()
    model_name = model or IFM_MODEL
    cooldown_until = 0.0
    consecutive_429 = 0
    METRICS.configure_planner(
        interval_s=interval,
        max_in_flight=max_in_flight,
        model_name=model_name,
    )

    async def _one_call(tick: int, instance: int) -> None:
        nonlocal latest_accepted, cooldown_until, consecutive_429
        if pause_event is not None and pause_event.is_set():
            return
        snap = get_snapshot()
        if session_notes:
            snap = {**snap, "Style": session_notes[-1]}
        snap = {**snap, "tick_id": tick, "instance": instance}
        prompt = fill_combat_prompt(
            game_state_json=json.dumps(snap, indent=2),
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
                temperature=0.2,
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

        if note:
            session_notes.append(note)
            if len(session_notes) > 10:
                session_notes.pop(0)
            if on_note:
                on_note(note)
                METRICS.set_last_note(note)

        if not intent:
            METRICS.flight_finish(
                instance=instance, step=tick, outcome="parse_fail", latency_s=elapsed
            )
            print(f"[Planner inst{instance} step{tick:03d}] parse fail ({elapsed:.2f}s): {raw[:80]}")
            return

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

        on_intent(
            {
                "dx": intent["mx"],
                "dy": intent["my"],
                "shoot": intent["fire"],
                "tick_id": tick_id,
            }
        )
        METRICS.flight_finish(
            instance=instance, step=tick_id, outcome="ok", latency_s=elapsed
        )
        print(
            f"[Planner inst{instance} step{tick_id:03d}] ok ({elapsed:.2f}s) -> {intent}"
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
