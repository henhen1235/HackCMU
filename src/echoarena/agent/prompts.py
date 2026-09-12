"""Prompt templates for the strategic planner."""

from __future__ import annotations

# Keep this compact — every token costs planner latency.
COMBAT_PROMPT = """Elite 2D duel AI. Reflex handles bullet dodge; you pick pressure.

MAP {MAP_WIDTH}x{MAP_HEIGHT} (+X right +Y down).
- borders=[N,E,S,W]: distance to MAP EDGES. These are OPEN BOUNDARIES — NOT cover.
  Never "hide behind" a border. Leaving a border is escape, not a peek.
- cover=[N,E,S,W]: distance to INTERIOR blocks only. ONLY these block shots / hide you.
- sit.border_pin = near a map edge (danger). sit.near_cover = near real blocks (use them).
Only reverse hard off a border if borders[] <20 (about to leave the map).

PROFILE:
{PLAYER_MEMORY}

PLAYBOOK — pick ONE that fits sit:
- close_gap: band=far → drive along to_enemy, shoot
- create_space: band=close or low hp → back off on -to_enemy
- cut_escape: enemy.vel strong → block their heading
- hold_mid_fire: band=mid, few threats → strafe perp, shoot when ready
- cover_peek: sit.near_cover set → use INTERIOR cover, then re-aim (never borders)
- wall_deny: sit.border_pin set → leave the map edge, force foe toward a border
- bait_push: you lead hp → fake retreat 1 beat, then close_gap
- reset_angle: same last_note/last_cmd → new to_enemy±perp axis

BANNED: "flank left/right", "feint", "hide behind border/edge/wall" when only borders are near.
last_note/last_cmd are anti-echo — change TACTIC or WHY.

OUTPUT (exactly):
<thinking>TACTIC: <playbook_name> | WHY: <one sit fact></thinking>
{{"dx":<-1..1>,"dy":<-1..1>,"shoot":true}}
shoot=true ALWAYS when bot.ready — never hold fire. Prefer to_enemy; hug INTERIOR cover only.

STATE:
{GAME_STATE_JSON}
"""


PROFILE_PROMPT = """Summarize this human duelist into EXACTLY 5 bullets (each starts with "- ").

Notes:
---
{OBSERVATIONS}
---
Outcome: {RESULT}

Cover: movement, firing, dodging, positioning, best exploit. Max 15 words each. No other text.
"""

_PLAYBOOK = (
    "close_gap",
    "create_space",
    "cut_escape",
    "hold_mid_fire",
    "cover_peek",
    "wall_deny",
    "bait_push",
    "reset_angle",
)


def situation_from_snap(snap: dict) -> dict:
    """Cheap local labels for the model + HUD fallbacks (no I/O)."""
    dist = float(snap.get("dist") or 0.0)
    if dist < 130:
        band = "close"
    elif dist < 260:
        band = "mid"
    else:
        band = "far"

    labels = ("N", "E", "S", "W")
    borders = snap.get("borders") or snap.get("walls") or [999, 999, 999, 999]
    cover = snap.get("cover") or [999, 999, 999, 999]
    border_pin = [labels[i] for i, d in enumerate(borders[:4]) if float(d) < 28]
    near_cover = [labels[i] for i, d in enumerate(cover[:4]) if float(d) < 90]
    threats = snap.get("threats") or []
    bot = snap.get("bot") or {}
    enemy = snap.get("enemy") or {}
    bhp = int(bot.get("hp", 100))
    ehp = int(enemy.get("hp", 100))
    ev = enemy.get("vel") or [0, 0]
    speed = (float(ev[0]) ** 2 + float(ev[1]) ** 2) ** 0.5
    return {
        "band": band,
        "threat_count": len(threats),
        "hp_delta": bhp - ehp,
        "border_pin": border_pin,
        "near_cover": near_cover,
        "enemy_speed": round(speed, 1),
        "bot_ready": bool(bot.get("ready", False)),
    }


def suggest_tactic(sit: dict, tick: int = 0) -> str:
    """Deterministic soft hint — helps diversity without an extra API call."""
    if sit.get("border_pin") or sit.get("edge_risk"):
        return "wall_deny"
    if sit.get("near_cover") or int(sit.get("threat_count") or 0) > 0:
        return "cover_peek"
    if sit.get("band") == "far":
        return "close_gap"
    if sit.get("band") == "close":
        return "create_space"
    if float(sit.get("enemy_speed") or 0) > 80:
        return "cut_escape"
    if int(sit.get("hp_delta") or 0) >= 20:
        return "bait_push"
    return "hold_mid_fire" if (tick % 4) < 2 else "reset_angle"


def playbook_note(snap: dict, mx: float, my: float, fire: bool) -> str:
    """Local HUD line when the model writes banned fluff or skips thinking."""
    sit = snap.get("sit") or situation_from_snap(snap)
    name = suggest_tactic(sit, int(snap.get("tick_id") or 0))
    why_bits = [f"band={sit.get('band')}"]
    if sit.get("border_pin") or sit.get("edge_risk"):
        pins = sit.get("border_pin") or sit.get("edge_risk") or []
        why_bits.append(f"border={','.join(pins)}")
    if sit.get("near_cover"):
        why_bits.append(f"cover={','.join(sit['near_cover'])}")
    if sit.get("threat_count"):
        why_bits.append(f"threats={sit['threat_count']}")
    if float(sit.get("enemy_speed") or 0) > 80:
        why_bits.append(f"foe_spd={sit['enemy_speed']}")
    why_bits.append(f"hpΔ={sit.get('hp_delta')}")
    shoot = "fire" if fire else "hold"
    return f"TACTIC: {name} | WHY: {', '.join(why_bits)} | vec=({mx:+.2f},{my:+.2f}) {shoot}"


def fill_combat_prompt(
    *,
    game_state_json: str,
    map_width: int,
    map_height: int,
    latency_ms: int,
    min_wall_distance: int,
    player_memory: str,
    tick_id: int = 0,
) -> str:
    del tick_id, latency_ms, min_wall_distance
    return COMBAT_PROMPT.format(
        MAP_WIDTH=map_width,
        MAP_HEIGHT=map_height,
        PLAYER_MEMORY=player_memory,
        GAME_STATE_JSON=game_state_json,
    )
