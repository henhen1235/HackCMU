/**
 * Isolated FPS demo server: static files + Grok planner proxy.
 * Does NOT import EchoArena Python — only reads ../.env for XAI_API_KEY.
 */
import express from "express";
import path from "path";
import { fileURLToPath } from "url";
import dotenv from "dotenv";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
dotenv.config({ path: path.resolve(__dirname, "../.env") });

const PORT = Number(process.env.FPS3D_PORT || 8787);
const XAI_API_KEY =
  (process.env.XAI_API_KEY || process.env.IFM_API_KEY || "").trim();
const XAI_BASE_URL = (
  process.env.XAI_BASE_URL ||
  process.env.IFM_BASE_URL ||
  "https://api.x.ai/v1"
).replace(/\/$/, "");
const XAI_MODEL =
  (process.env.XAI_MODEL || process.env.IFM_MODEL || "").trim() ||
  "grok-4.20-0309-non-reasoning";

const PLAYBOOK = `Elite FPS duel AI on a flat XZ arena. Reflex is local; you pick pressure.

STATE: bot/player {x,z,yaw,hp}, dist, near_cover, prefer_tactic, last_note.
last_note is ANTI-ECHO — your NEW TACTIC name OR WHY fact MUST differ.
prefer_tactic is a soft hint for this tick — bias toward it when it fits.

PLAYBOOK — pick ONE that fits NOW (recompute from dist / near_cover / hp):
- close_gap: dist far → push toward player
- create_space: dist close or low hp → back off
- cut_escape: player speed high → intercept
- hold_mid_fire: mid range → strafe + shoot
- cover_peek: near_cover → use crate then re-aim
- reset_angle: repeating last_note → flip approach axis

BANNED as the whole note: copying last_note verbatim, "flank left/right", "feint".
Always shoot=true.

OUTPUT exactly:
<thinking>TACTIC: <name> | WHY: <one CURRENT sit fact: dist/hp/cover></thinking>
{"dx":<-1..1>,"dz":<-1..1>,"shoot":true}
dx=+X, dz=+Z (Three.js XZ).`;

function parseIntent(raw) {
  const noteMatch = raw.match(
    /<(?:note|thinking)>\s*([\s\S]*?)\s*<\/(?:note|thinking)>/i
  );
  let note = noteMatch ? noteMatch[1].trim() : null;
  const cleaned = raw.replace(
    /<(?:note|thinking)>[\s\S]*?<\/(?:note|thinking)>/gi,
    ""
  );
  const jsonMatch = cleaned.match(/\{[^{}]*\}/);
  if (!jsonMatch) {
    return { ok: false, error: "parse_fail", raw: raw.slice(0, 200) };
  }
  let payload;
  try {
    payload = JSON.parse(jsonMatch[0].replace(/,\s*([}\]])/g, "$1"));
  } catch {
    return { ok: false, error: "parse_fail", raw: raw.slice(0, 200) };
  }
  const dx = Number(payload.dx ?? payload.mx ?? 0);
  const dz = Number(payload.dz ?? payload.dy ?? payload.my ?? 0);
  if (!note) {
    note = `TACTIC: close_gap | WHY: fallback vec=(${dx.toFixed(2)},${dz.toFixed(2)})`;
  }
  return {
    ok: true,
    dx: Math.max(-1, Math.min(1, dx)),
    dz: Math.max(-1, Math.min(1, dz)),
    shoot: true,
    note: note.slice(0, 140),
  };
}

const app = express();
app.use(express.json({ limit: "32kb" }));
app.use(express.static(path.join(__dirname, "public")));

app.get("/api/health", (_req, res) => {
  res.json({
    ok: true,
    model: XAI_MODEL,
    hasKey: Boolean(XAI_API_KEY),
  });
});

app.post("/api/plan", async (req, res) => {
  if (!XAI_API_KEY) {
    res.status(503).json({ ok: false, error: "missing_XAI_API_KEY" });
    return;
  }
  const snap = req.body || {};
  const prompt = `${PLAYBOOK}\n\nSTATE:\n${JSON.stringify(snap)}`;
  const started = Date.now();
  try {
    const r = await fetch(`${XAI_BASE_URL}/chat/completions`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${XAI_API_KEY}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        model: XAI_MODEL,
        messages: [{ role: "user", content: prompt }],
        max_tokens: 220,
        temperature: 0.55,
      }),
      signal: AbortSignal.timeout(8000),
    });
    const latency_ms = Date.now() - started;
    if (!r.ok) {
      const text = await r.text();
      res.status(r.status).json({
        ok: false,
        error: "upstream",
        status: r.status,
        detail: text.slice(0, 240),
        latency_ms,
      });
      return;
    }
    const data = await r.json();
    const raw = data?.choices?.[0]?.message?.content || "";
    const parsed = parseIntent(raw);
    res.json({ ...parsed, latency_ms, model: XAI_MODEL });
  } catch (err) {
    res.status(502).json({
      ok: false,
      error: String(err?.message || err),
      latency_ms: Date.now() - started,
    });
  }
});

app.listen(PORT, () => {
  console.log(`[fps3d] http://localhost:${PORT}`);
  console.log(`[fps3d] model=${XAI_MODEL} key=${XAI_API_KEY ? "yes" : "MISSING"}`);
  console.log("[fps3d] isolated from EchoArena — tank demo unchanged");
});
