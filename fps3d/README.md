# EchoArena FPS (3D vertical slice)

Isolated **Three.js** first-person duel that proves the same Optimization idea as the 2D tank game:

- **LLM** — async Grok planner returns `{dx, dz, shoot}` + tactic note  
- **LOCAL** — blind rush (no strategy)  
- **Ablation** — `P` pause, `Tab` toggles LOCAL ↔ LLM  

This folder does **not** import `src/echoarena`. If this demo breaks, the tank game is untouched.

## Run

From repo root (uses the same `.env` `XAI_API_KEY`):

```bash
cd fps3d
npm install
npm start
```

Open [http://localhost:8787](http://localhost:8787).

## Controls

| Input | Action |
| --- | --- |
| Click | Lock mouse / fire |
| WASD | Move |
| Mouse | Look |
| **P** | Pause / resume |
| **Tab** | While paused: LOCAL ↔ LLM |
| R | Restart after match end |

## Demo script

1. Start in **LLM** — watch tactic note + latency  
2. **P** → **Tab** → **LOCAL** → **P** — bot rushes straight at you  
3. Pause → Tab → **LLM** → resume — planner tactics return  

## Layout

```
fps3d/
  package.json
  server.mjs      # static + POST /api/plan (Grok proxy)
  public/
    index.html
    style.css
    main.js       # Three.js arena + combat + ablation
  README.md
```
