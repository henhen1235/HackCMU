"""Judge-facing metrics window — only what proves the system works."""

from __future__ import annotations

import pygame

from echoarena.metrics import MetricsSnapshot

DASH_W = 340
DASH_H = 420

BG = (12, 12, 12)
INK = (220, 220, 220)
DIM = (130, 130, 130)
LINE = (40, 40, 40)


def _font(size: int, bold: bool = False) -> pygame.font.Font:
    for name in ("Menlo", "SF Mono", "Consolas", "monospace"):
        path = pygame.font.match_font(name, bold=bold)
        if path:
            return pygame.font.Font(path, size)
    return pygame.font.SysFont("monospace", size, bold=bold)


def _row(
    surf: pygame.Surface,
    label: str,
    value: str,
    x: int,
    y: int,
    fl: pygame.font.Font,
    fv: pygame.font.Font,
) -> int:
    surf.blit(fl.render(label, True, DIM), (x, y))
    surf.blit(fv.render(value, True, INK), (x + 120, y))
    return y + 18


def _head(surf: pygame.Surface, text: str, x: int, y: int, f: pygame.font.Font) -> int:
    y += 8
    surf.blit(f.render(text, True, DIM), (x, y))
    pygame.draw.line(surf, LINE, (x, y + 14), (DASH_W - x, y + 14), 1)
    return y + 22


def render_metrics_dashboard(snap: MetricsSnapshot) -> pygame.Surface:
    """Show only judge-relevant optimization signals."""
    surf = pygame.Surface((DASH_W, DASH_H))
    surf.fill(BG)

    fl = _font(13)
    fv = _font(13, bold=True)
    fh = _font(12, bold=True)

    x = 18
    y = 16

    model = snap.model_name if len(snap.model_name) < 34 else snap.model_name[:31] + "…"
    surf.blit(fh.render("LIVE OPTIMIZATION", True, INK), (x, y))
    y += 18
    surf.blit(fl.render(model, True, DIM), (x, y))
    y += 16
    surf.blit(
        fl.render(
            f"{snap.arena_label} · {snap.difficulty_label} · {snap.match_elapsed_s:.0f}s",
            True,
            DIM,
        ),
        (x, y),
    )

    y = _head(surf, "LLM RETURN", x, y, fh)
    if snap.last_instance >= 0:
        y = _row(surf, "instance", str(snap.last_instance), x, y, fl, fv)
        y = _row(surf, "step", f"{snap.last_step:03d}", x, y, fl, fv)
        y = _row(surf, "status", snap.last_outcome, x, y, fl, fv)
    else:
        y = _row(surf, "instance", "—", x, y, fl, fv)
        y = _row(surf, "step", "—", x, y, fl, fv)
        y = _row(surf, "status", "waiting", x, y, fl, fv)
    y = _row(surf, "latency", f"{snap.latency_last_ms:.0f} ms", x, y, fl, fv)
    y = _row(surf, "p50", f"{snap.latency_p50_ms:.0f} ms", x, y, fl, fv)

    y = _head(surf, "CONTROL", x, y, fh)
    age = f"{snap.intent_age_s:.2f}s" if snap.intent_age_s < 900 else "—"
    y = _row(surf, "intent age", age, x, y, fl, fv)
    y = _row(
        surf,
        "vector",
        f"{snap.intent_mx:+.2f}  {snap.intent_my:+.2f}",
        x,
        y,
        fl,
        fv,
    )
    y = _row(surf, "llm drive", f"{snap.llm_influence_pct:.0f}%", x, y, fl, fv)
    y = _row(
        surf,
        "pipeline",
        f"{snap.in_flight} flying · {snap.ok} ok · {snap.rate_limited}×429",
        x,
        y,
        fl,
        fv,
    )
    y = _row(surf, "success", f"{snap.success_rate_pct:.0f}%", x, y, fl, fv)

    y = _head(surf, "FAIR DUEL", x, y, fh)
    y = _row(surf, "hp", f"you {snap.player_hp}  echo {snap.bot_hp}", x, y, fl, fv)
    y = _row(surf, "speed", f"{snap.move_speed:.0f} both", x, y, fl, fv)
    y = _row(surf, "cooldown", f"{snap.fire_cooldown_s:.2f}s both", x, y, fl, fv)
    y = _row(surf, "fps", f"{snap.fps:.0f}", x, y, fl, fv)

    note = snap.last_note or "—"
    if len(note) > 34:
        note = note[:31] + "…"
    y = _head(surf, "NOTE", x, y, fh)
    surf.blit(fl.render(note, True, INK), (x, y))

    return surf


class MetricsWindow:
    """Separate SDL window for metrics (not glued to the arena)."""

    def __init__(self) -> None:
        from pygame._sdl2.video import Renderer, Window

        try:
            from pygame._sdl2.video import Texture
        except ImportError:
            from pygame._sdl2 import Texture  # type: ignore

        self._Texture = Texture
        self.window = Window("EchoArena · metrics", size=(DASH_W, DASH_H))
        self.window.resizable = False
        self.renderer = Renderer(self.window)
        self._alive = True

    def draw(self, snap: MetricsSnapshot) -> None:
        if not self._alive:
            return
        try:
            card = render_metrics_dashboard(snap)
            tex = self._Texture.from_surface(self.renderer, card)
            self.renderer.draw_color = (*BG, 255)
            self.renderer.clear()
            tex.draw(dstrect=(0, 0, DASH_W, DASH_H))
            self.renderer.present()
        except Exception:  # noqa: BLE001
            self._alive = False

    def destroy(self) -> None:
        if not self._alive:
            return
        self._alive = False
        try:
            self.window.destroy()
        except Exception:  # noqa: BLE001
            pass
