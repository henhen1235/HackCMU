# EchoArena

Real-time arena duel where **you** fight an adaptive LLM opponent.

The model plans strategy asynchronously. A local **tactics** layer executes
frame-perfect dodges and wall avoidance so gameplay never waits on inference.

## Architecture

1. **Simulation (60 FPS)** — movement, projectiles, collisions, rendering
2. **Planner (async xAI Grok)** — pipelined intent packets `{mx, my, fire}`
3. **Tactics reflex** — time-of-closest-approach dodge + soft wall push
4. **Ablation** — pause and switch **LOCAL** (reflex only) vs **LLM** (pipelined)
5. **Profile memory** — end-of-match summaries injected into later prompts

## Controls

| Input | Action |
| --- | --- |
| WASD / arrows | Move |
| Mouse | Aim |
| LMB / Space | Fire |
| **P** | Pause / resume match |
| **Tab** | While paused: toggle **LOCAL** ↔ **LLM** |
| 1 / 2 / 3 | Switch arena mid-match (while playing) |
| R / Enter / Space | After game over → back to menu |
| ESC | Quit |

A second window shows judge-facing metrics (mode, latency, LLM drive %, fair duel stats).

## Demo tip (Optimization)

1. Start in **LLM** — watch intent age, latency, and `llm drive %`
2. Press **P** — match freezes; planner stops spending
3. Press **Tab** — switch to **LOCAL**
4. Press **P** — resume; bot still fights, `llm drive` drops toward 0
5. Pause → Tab → **LLM** → resume — intents return

Fair rules stay identical (same speed & cooldown) in both modes.

## Setup

```bash
cd hackCMU
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# put your xAI API key in .env (XAI_API_KEY)
python run.py
```

Requires Python 3.10+ and an `XAI_API_KEY` from [xAI](https://console.x.ai/).
Default model: `grok-4.20-0309-non-reasoning` via `https://api.x.ai/v1/chat/completions`
(lowest-latency non-reasoning Grok for the live control loop).

## Layout

```
run.py
src/echoarena/
  config.py          # difficulties, arenas, palette
  models.py          # fighters, projectiles
  physics.py         # collisions / bounds
  render.py          # amber-steel UI
  game.py            # main loop + pause/ablation
  metrics.py         # live telemetry
  metrics_dashboard.py
  agent/ifm_client.py # xAI Grok OpenAI-compatible HTTP client
  agent/planner.py   # pipelined Grok calls
  agent/prompts.py
  agent/memory.py    # cross-match profile
  tactics/reflex.py  # per-frame execution
data/player_profile.txt
```

## Hackathon pitch

LLMs are strong at strategy and weak at frame timing. EchoArena splits those
jobs: the planner thinks ahead; tactics reacts now. Pause + LOCAL/LLM ablation
makes the Optimization claim obvious — FPS stays live while you prove the
pipelined brain is optional, not load-bearing for the 60 FPS loop.
