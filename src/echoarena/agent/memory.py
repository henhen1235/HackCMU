"""Cross-match player profiling persisted under data/."""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from echoarena.agent.ifm_client import IFMError, chat_completion
from echoarena.agent.prompts import PROFILE_PROMPT

_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(_ROOT / ".env")

PROFILE_FILE = _ROOT / "data" / "player_profile.txt"
MAX_SESSIONS = 8


def load_profile() -> str:
    if not PROFILE_FILE.exists():
        return ""
    try:
        return PROFILE_FILE.read_text(encoding="utf-8").strip()
    except OSError as exc:
        print(f"[Profile] read failed: {exc}")
        return ""


def wrap_profile_for_prompt(raw: str) -> str:
    if not raw.strip():
        return "No prior matches on file."
    if len(raw) > 1500:
        raw = "…older matches truncated…\n" + raw[-1500:]
    return raw


def _trim_sessions() -> None:
    if not PROFILE_FILE.exists():
        return
    try:
        text = PROFILE_FILE.read_text(encoding="utf-8")
        sep = "═" * 60
        chunks = [c.strip() for c in text.split(sep) if c.strip()]
        if len(chunks) > MAX_SESSIONS:
            kept = chunks[-MAX_SESSIONS:]
            PROFILE_FILE.write_text(("\n" + sep + "\n").join(kept) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"[Profile] trim failed: {exc}")


def _fallback_summary(notes: list[str], result: str) -> str:
    lines = [f"- Match result: {result}"]
    if notes:
        lines.append("- Planner notes (raw, summary API unavailable):")
        lines.extend(f"  - {n}" for n in notes[-40:])
    else:
        lines.append("- No planner notes captured (rate-limit or quiet match).")
        lines.append("- Keep pressure high; exploit last known habits if any.")
    return "\n".join(lines)


async def _summarize(notes: list[str], result: str) -> str:
    if not notes:
        return ""
    prompt = PROFILE_PROMPT.format(
        OBSERVATIONS="\n".join(notes[-80:]),
        RESULT=result,
    )
    try:
        return await chat_completion(
            [{"role": "user", "content": prompt}],
            max_tokens=300,
        )
    except IFMError as exc:
        print(f"[Profile] summarize error: {exc}")
        return ""
    except Exception as exc:  # noqa: BLE001
        print(f"[Profile] summarize error: {exc}")
        return ""


def persist_match(notes: list[str], winner: str) -> None:
    """Always append a session entry — LLM summary preferred, raw notes as fallback."""
    result = "Bot won" if winner == "bot" else "Human won"
    print(f"[Profile] saving session ({len(notes)} notes, {result})…")

    summary = ""
    if notes:
        summary = asyncio.run(_summarize(notes, result))
    if not summary:
        summary = _fallback_summary(notes, result)
        print("[Profile] wrote fallback notes (no LLM summary).")

    PROFILE_FILE.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    sep = "═" * 60
    entry = f"{sep}\nMATCH: {stamp}  |  {result}\n{summary}\n"
    try:
        with PROFILE_FILE.open("a", encoding="utf-8") as handle:
            handle.write(entry)
        _trim_sessions()
        print(f"[Profile] saved → {PROFILE_FILE}")
    except OSError as exc:
        print(f"[Profile] write failed: {exc}")
