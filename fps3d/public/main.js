/**
 * EchoArena FPS vertical slice — Three.js client.
 * Isolated from src/echoarena; talks only to /api/plan on this server.
 */
import * as THREE from "three";

const ARENA = 36;
const HALF = ARENA / 2;
const SPEED = 9;
const PROJ_SPEED = 42;
const COOLDOWN = 0.22;
const MAX_HP = 100;
const HIT = 10;
const PLAYER_H = 1.6;
const BODY_R = 0.45;
const PLAN_INTERVAL = 0.35;
const MAX_IN_FLIGHT = 3;
const INTENT_MAX_AGE = 3.0;
const TACTIC_ROTATE = [
  "close_gap",
  "hold_mid_fire",
  "cover_peek",
  "create_space",
  "cut_escape",
  "reset_angle",
];

const COVER = [
  { x: -6, z: -4, w: 2.2, d: 2.2, h: 2.4 },
  { x: 7, z: 5, w: 2.4, d: 2.0, h: 2.2 },
  { x: 0, z: 10, w: 3.0, d: 1.4, h: 2.0 },
  { x: -10, z: 8, w: 1.8, d: 2.8, h: 2.5 },
];

const hud = {
  mode: document.getElementById("mode"),
  php: document.getElementById("php"),
  bhp: document.getElementById("bhp"),
  phpFill: document.getElementById("php-fill"),
  bhpFill: document.getElementById("bhp-fill"),
  iage: document.getElementById("iage"),
  lat: document.getElementById("lat"),
  inflight: document.getElementById("inflight"),
  note: document.getElementById("note"),
  overlay: document.getElementById("overlay"),
  overlayText: document.getElementById("overlay-text"),
  end: document.getElementById("end"),
  endText: document.getElementById("end-text"),
};

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x151210);
scene.fog = new THREE.Fog(0x151210, 28, 70);

const camera = new THREE.PerspectiveCamera(
  75,
  window.innerWidth / window.innerHeight,
  0.05,
  120
);
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.shadowMap.enabled = true;
document.body.appendChild(renderer.domElement);

scene.add(new THREE.AmbientLight(0x6a5a48, 0.55));
const sun = new THREE.DirectionalLight(0xffd8a8, 1.05);
sun.position.set(12, 22, 8);
sun.castShadow = true;
scene.add(sun);

function addArena() {
  const floor = new THREE.Mesh(
    new THREE.PlaneGeometry(ARENA, ARENA),
    new THREE.MeshStandardMaterial({ color: 0x2a241c, roughness: 0.92 })
  );
  floor.rotation.x = -Math.PI / 2;
  floor.receiveShadow = true;
  scene.add(floor);

  const grid = new THREE.GridHelper(ARENA, 18, 0x3d3428, 0x2c261f);
  grid.position.y = 0.01;
  scene.add(grid);

  const wallMat = new THREE.MeshStandardMaterial({
    color: 0x4a3f32,
    roughness: 0.85,
  });
  const wallH = 4;
  const wallT = 0.6;
  const walls = [
    [0, HALF, ARENA + wallT, wallT],
    [0, -HALF, ARENA + wallT, wallT],
    [HALF, 0, wallT, ARENA + wallT],
    [-HALF, 0, wallT, ARENA + wallT],
  ];
  for (const [x, z, w, d] of walls) {
    const m = new THREE.Mesh(
      new THREE.BoxGeometry(w, wallH, d),
      wallMat
    );
    m.position.set(x, wallH / 2, z);
    m.castShadow = true;
    m.receiveShadow = true;
    scene.add(m);
  }

  const coverMat = new THREE.MeshStandardMaterial({
    color: 0x6b5740,
    roughness: 0.7,
  });
  for (const c of COVER) {
    const m = new THREE.Mesh(
      new THREE.BoxGeometry(c.w, c.h, c.d),
      coverMat
    );
    m.position.set(c.x, c.h / 2, c.z);
    m.castShadow = true;
    m.receiveShadow = true;
    scene.add(m);
  }
}
addArena();

function makeFighter(color, isPlayer) {
  const root = new THREE.Group();
  const body = new THREE.Mesh(
    new THREE.CapsuleGeometry(BODY_R, 0.9, 4, 8),
    new THREE.MeshStandardMaterial({ color, roughness: 0.55 })
  );
  body.position.y = 0.95;
  body.castShadow = true;
  root.add(body);
  if (!isPlayer) {
    const head = new THREE.Mesh(
      new THREE.SphereGeometry(0.28, 12, 12),
      new THREE.MeshStandardMaterial({ color: 0xc45a3a })
    );
    head.position.y = 1.75;
    root.add(head);
  }
  scene.add(root);
  return {
    root,
    x: 0,
    z: 0,
    yaw: 0,
    pitch: 0,
    hp: MAX_HP,
    cd: 0,
    vx: 0,
    vz: 0,
  };
}

const player = makeFighter(0xd4b46a, true);
const bot = makeFighter(0xb84a38, false);
player.x = -10;
player.z = 0;
player.yaw = 0;
bot.x = 10;
bot.z = 0;
bot.yaw = Math.PI / 2; // face toward -X (player spawn)

const projectiles = [];
const keys = new Set();
let pointerLocked = false;
let paused = false;
let agentMode = "LLM"; // LOCAL | LLM
let gameOver = null; // 'player' | 'bot' | null
let lastNote = "Waiting for planner…";
let lastLatency = null;
let intentAt = 0;
let intentDx = 0;
let intentDz = 0;
let hasIntent = false;
let inFlight = 0;
let planAcc = 0;
let lastPlanNote = "";
let planSeq = 0;
let latestAccepted = 0;
let planStep = 0;

function clampArena(x, z) {
  const lim = HALF - BODY_R - 0.35;
  return {
    x: Math.max(-lim, Math.min(lim, x)),
    z: Math.max(-lim, Math.min(lim, z)),
  };
}

function hitsCover(x, z) {
  for (const c of COVER) {
    if (
      Math.abs(x - c.x) < c.w / 2 + BODY_R &&
      Math.abs(z - c.z) < c.d / 2 + BODY_R
    ) {
      return true;
    }
  }
  return false;
}

function tryMove(ent, dx, dz, dt) {
  const len = Math.hypot(dx, dz) || 1;
  const nx = ent.x + (dx / len) * SPEED * dt;
  const nz = ent.z + (dz / len) * SPEED * dt;
  if (!hitsCover(nx, ent.z)) ent.x = nx;
  if (!hitsCover(ent.x, nz)) ent.z = nz;
  const c = clampArena(ent.x, ent.z);
  ent.x = c.x;
  ent.z = c.z;
}

function nearCover(x, z) {
  for (const c of COVER) {
    const d = Math.hypot(x - c.x, z - c.z);
    if (d < 4.5) return true;
  }
  return false;
}

function spawnShot(from, yaw, pitch, owner) {
  // Match Three.js camera: yaw=0 looks down -Z
  const dir = new THREE.Vector3(
    -Math.sin(yaw) * Math.cos(pitch),
    Math.sin(pitch),
    -Math.cos(yaw) * Math.cos(pitch)
  ).normalize();
  const mesh = new THREE.Mesh(
    new THREE.SphereGeometry(0.12, 8, 8),
    new THREE.MeshBasicMaterial({
      color: owner === "player" ? 0xf0c878 : 0xff6a4a,
    })
  );
  const ox = from.x + dir.x * 0.7;
  const oy = PLAYER_H + dir.y * 0.2;
  const oz = from.z + dir.z * 0.7;
  mesh.position.set(ox, oy, oz);
  scene.add(mesh);
  projectiles.push({
    mesh,
    vx: dir.x * PROJ_SPEED,
    vy: dir.y * PROJ_SPEED,
    vz: dir.z * PROJ_SPEED,
    owner,
    life: 2.2,
  });
}

function faceYaw(from, to) {
  // Yaw so that look direction (-sin, -cos) points toward target
  return Math.atan2(-(to.x - from.x), -(to.z - from.z));
}

function syncMeshes() {
  player.root.position.set(player.x, 0, player.z);
  player.root.rotation.y = player.yaw;
  bot.root.position.set(bot.x, 0, bot.z);
  bot.root.rotation.y = bot.yaw;
  camera.position.set(player.x, PLAYER_H, player.z);
  camera.rotation.order = "YXZ";
  camera.rotation.y = player.yaw;
  camera.rotation.x = player.pitch;
}

function resetMatch() {
  player.x = -10;
  player.z = 0;
  player.yaw = 0;
  player.pitch = 0;
  player.hp = MAX_HP;
  player.cd = 0;
  bot.x = 10;
  bot.z = 0;
  bot.yaw = Math.PI / 2;
  bot.hp = MAX_HP;
  bot.cd = 0;
  for (const p of projectiles) scene.remove(p.mesh);
  projectiles.length = 0;
  gameOver = null;
  hasIntent = false;
  intentAt = 0;
  planStep = 0;
  latestAccepted = 0;
  lastNote = agentMode === "LLM" ? "Planner warming…" : "LOCAL blind rush";
  hud.end.classList.add("hidden");
  updateHud();
}

function updateHud() {
  hud.mode.textContent = agentMode;
  hud.mode.style.borderColor = agentMode === "LLM" ? "#d29646" : "#888";
  const php = Math.max(0, Math.round(player.hp));
  const bhp = Math.max(0, Math.round(bot.hp));
  hud.php.textContent = String(php);
  hud.bhp.textContent = String(bhp);
  hud.phpFill.style.width = `${(php / MAX_HP) * 100}%`;
  hud.bhpFill.style.width = `${(bhp / MAX_HP) * 100}%`;
  if (hasIntent && agentMode === "LLM") {
    const age = (performance.now() / 1000 - intentAt).toFixed(2);
    hud.iage.textContent = `${age}s`;
  } else {
    hud.iage.textContent = agentMode === "LOCAL" ? "n/a" : "—";
  }
  hud.lat.textContent =
    lastLatency != null ? `${Math.round(lastLatency)}ms` : "—";
  hud.inflight.textContent = String(inFlight);
  // Always show step so judges see refreshes even if tactic text repeats
  const noteBit = lastNote || "—";
  hud.note.textContent =
    agentMode === "LLM" && planStep > 0
      ? `#${planStep} ${noteBit}`
      : noteBit;
  if (paused && !gameOver) {
    hud.overlay.classList.remove("hidden");
    hud.overlayText.textContent = `PAUSED · ${agentMode}`;
  } else {
    hud.overlay.classList.add("hidden");
  }
}

function canLaunchPlan() {
  return (
    !paused &&
    !gameOver &&
    agentMode === "LLM" &&
    inFlight < MAX_IN_FLIGHT
  );
}

async function requestPlan() {
  if (!canLaunchPlan()) return;
  inFlight += 1;
  const seq = ++planSeq;
  const dist = Math.hypot(player.x - bot.x, player.z - bot.z);
  const spd = Math.hypot(player.vx, player.vz);
  let prefer;
  if (nearCover(bot.x, bot.z)) prefer = "cover_peek";
  else if (dist > 14) prefer = "close_gap";
  else if (dist < 6) prefer = "create_space";
  else if (spd > 6) prefer = "cut_escape";
  else prefer = TACTIC_ROTATE[planSeq % TACTIC_ROTATE.length];

  const snap = {
    bot: {
      x: +bot.x.toFixed(2),
      z: +bot.z.toFixed(2),
      yaw: +bot.yaw.toFixed(2),
      hp: bot.hp,
    },
    player: {
      x: +player.x.toFixed(2),
      z: +player.z.toFixed(2),
      yaw: +player.yaw.toFixed(2),
      hp: player.hp,
      vx: +player.vx.toFixed(2),
      vz: +player.vz.toFixed(2),
      speed: +spd.toFixed(2),
    },
    dist: +dist.toFixed(2),
    near_cover: nearCover(bot.x, bot.z),
    prefer_tactic: prefer,
    last_note: lastPlanNote || undefined,
    do_not_repeat:
      "Change TACTIC name or WHY. Cite current dist/hp/near_cover.",
    tick: seq,
  };
  try {
    const r = await fetch("/api/plan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(snap),
    });
    const data = await r.json();
    if (seq < latestAccepted) return;
    lastLatency = data.latency_ms ?? null;
    if (data.ok) {
      latestAccepted = seq;
      planStep += 1;
      intentDx = data.dx;
      intentDz = data.dz;
      hasIntent = true;
      intentAt = performance.now() / 1000;
      let note = data.note || lastNote;
      if (
        lastPlanNote &&
        note.trim().toLowerCase() === lastPlanNote.trim().toLowerCase()
      ) {
        note = `TACTIC: ${prefer} | WHY: dist=${dist.toFixed(1)} hpΔ=${bot.hp - player.hp}`;
      }
      lastNote = note;
      lastPlanNote = note;
    } else {
      lastNote = `Planner: ${data.error || "fail"}`;
    }
  } catch (e) {
    lastNote = `Planner offline: ${e.message}`;
  } finally {
    inFlight -= 1;
    updateHud();
  }
}

function updateBot(dt) {
  bot.cd = Math.max(0, bot.cd - dt);
  const toPlayerX = player.x - bot.x;
  const toPlayerZ = player.z - bot.z;
  const dist = Math.hypot(toPlayerX, toPlayerZ) || 1;

  let mx = 0;
  let mz = 0;
  const age = hasIntent ? performance.now() / 1000 - intentAt : 999;

  if (agentMode === "LOCAL") {
    mx = toPlayerX / dist;
    mz = toPlayerZ / dist;
    lastNote = "LOCAL: blind rush";
  } else if (hasIntent && age < INTENT_MAX_AGE) {
    const mag = Math.hypot(intentDx, intentDz) || 1;
    mx = intentDx / mag;
    mz = intentDz / mag;
  } else if (hasIntent) {
    // Stale LLM — still chase with last bias
    mx = intentDx * 0.35 + (toPlayerX / dist) * 0.65;
    mz = intentDz * 0.35 + (toPlayerZ / dist) * 0.65;
  } else {
    mx = toPlayerX / dist;
    mz = toPlayerZ / dist;
  }

  tryMove(bot, mx, mz, dt);
  bot.yaw = faceYaw(bot, player);

  // Always shoot when cooldown ready (fair aggression)
  if (bot.cd <= 0 && dist < 28) {
    const yaw = faceYaw(bot, player);
    const pitch = Math.atan2(
      PLAYER_H - PLAYER_H,
      Math.hypot(player.x - bot.x, player.z - bot.z)
    );
    // slight ballistic lead
    const t = dist / PROJ_SPEED;
    const aim = {
      x: player.x + player.vx * t,
      z: player.z + player.vz * t,
    };
    const ay = faceYaw(bot, aim);
    spawnShot(bot, ay, pitch, "bot");
    bot.cd = COOLDOWN;
  }
}

function updatePlayer(dt) {
  player.cd = Math.max(0, player.cd - dt);
  let dx = 0;
  let dz = 0;
  // dz = forward input (+1 = move where camera looks)
  if (keys.has("KeyW") || keys.has("ArrowUp")) dz += 1;
  if (keys.has("KeyS") || keys.has("ArrowDown")) dz -= 1;
  if (keys.has("KeyA") || keys.has("ArrowLeft")) dx -= 1;
  if (keys.has("KeyD") || keys.has("ArrowRight")) dx += 1;

  // Camera yaw=0 looks down -Z — keep movement aligned with that
  const forwardX = -Math.sin(player.yaw);
  const forwardZ = -Math.cos(player.yaw);
  const rightX = Math.cos(player.yaw);
  const rightZ = -Math.sin(player.yaw);
  const mx = forwardX * dz + rightX * dx;
  const mz = forwardZ * dz + rightZ * dx;
  player.vx = 0;
  player.vz = 0;
  if (Math.hypot(mx, mz) > 0.01) {
    tryMove(player, mx, mz, dt);
    player.vx = (mx / Math.hypot(mx, mz)) * SPEED;
    player.vz = (mz / Math.hypot(mx, mz)) * SPEED;
  }
}

function updateProjectiles(dt) {
  for (let i = projectiles.length - 1; i >= 0; i--) {
    const p = projectiles[i];
    p.life -= dt;
    p.mesh.position.x += p.vx * dt;
    p.mesh.position.y += p.vy * dt;
    p.mesh.position.z += p.vz * dt;

    const x = p.mesh.position.x;
    const y = p.mesh.position.y;
    const z = p.mesh.position.z;
    let dead = p.life <= 0 || y < 0 || y > 8 || Math.abs(x) > HALF || Math.abs(z) > HALF;

    if (!dead) {
      for (const c of COVER) {
        if (
          Math.abs(x - c.x) < c.w / 2 &&
          y < c.h &&
          Math.abs(z - c.z) < c.d / 2
        ) {
          dead = true;
          break;
        }
      }
    }

    if (!dead && p.owner === "player") {
      if (
        Math.hypot(x - bot.x, z - bot.z) < BODY_R + 0.25 &&
        y > 0.3 &&
        y < 2.1
      ) {
        bot.hp -= HIT;
        dead = true;
      }
    }
    if (!dead && p.owner === "bot") {
      if (
        Math.hypot(x - player.x, z - player.z) < BODY_R + 0.25 &&
        y > 0.3 &&
        y < 2.1
      ) {
        player.hp -= HIT;
        dead = true;
      }
    }

    if (dead) {
      scene.remove(p.mesh);
      projectiles.splice(i, 1);
    }
  }

  if (player.hp <= 0 && !gameOver) {
    gameOver = "bot";
    hud.endText.textContent = "Bot wins";
    hud.end.classList.remove("hidden");
  } else if (bot.hp <= 0 && !gameOver) {
    gameOver = "player";
    hud.endText.textContent = "You win";
    hud.end.classList.remove("hidden");
  }
}

window.addEventListener("resize", () => {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
});

function tryFirePlayer() {
  if (paused || gameOver || !pointerLocked) return;
  if (player.cd > 0) return;
  spawnShot(player, player.yaw, player.pitch, "player");
  player.cd = COOLDOWN;
}

document.addEventListener("click", () => {
  if (gameOver) {
    resetMatch();
    return;
  }
  if (!pointerLocked) {
    renderer.domElement.requestPointerLock();
    return;
  }
  tryFirePlayer();
});

document.addEventListener("pointerlockchange", () => {
  pointerLocked = document.pointerLockElement === renderer.domElement;
});

document.addEventListener("mousedown", (e) => {
  if (e.button !== 0) return;
  if (!pointerLocked || paused || gameOver) return;
  tryFirePlayer();
});

document.addEventListener("mousemove", (e) => {
  if (!pointerLocked || paused || gameOver) return;
  player.yaw -= e.movementX * 0.0022;
  player.pitch -= e.movementY * 0.0022;
  player.pitch = Math.max(-1.2, Math.min(1.2, player.pitch));
});

document.addEventListener("keydown", (e) => {
  keys.add(e.code);
  if (e.code === "Space") {
    e.preventDefault();
    tryFirePlayer();
  }
  if (e.code === "KeyP") {
    if (gameOver) return;
    paused = !paused;
    updateHud();
  }
  if (e.code === "Tab") {
    e.preventDefault();
    if (paused && !gameOver) {
      agentMode = agentMode === "LLM" ? "LOCAL" : "LLM";
      if (agentMode === "LOCAL") {
        hasIntent = false;
        lastNote = "LOCAL: blind rush";
      } else {
        lastNote = "LLM: planner resuming…";
        planAcc = PLAN_INTERVAL;
      }
      updateHud();
    }
  }
  if (e.code === "KeyR" && gameOver) resetMatch();
});

document.addEventListener("keyup", (e) => keys.delete(e.code));

fetch("/api/health")
  .then((r) => r.json())
  .then((h) => {
    if (!h.hasKey) lastNote = "Missing XAI_API_KEY in repo .env";
    else lastNote = `Ready · ${h.model}`;
    updateHud();
  })
  .catch(() => {
    lastNote = "Server health check failed";
    updateHud();
  });

let prev = performance.now() / 1000;
function frame(nowMs) {
  const now = nowMs / 1000;
  let dt = Math.min(0.05, now - prev);
  prev = now;

  if (!paused && !gameOver) {
    updatePlayer(dt);
    updateBot(dt);
    updateProjectiles(dt);
    if (agentMode === "LLM") {
      planAcc += dt;
      if (planAcc >= PLAN_INTERVAL && canLaunchPlan()) {
        planAcc = 0;
        void requestPlan();
      }
    }
  }

  syncMeshes();
  updateHud();
  renderer.render(scene, camera);
  requestAnimationFrame(frame);
}

updateHud();
syncMeshes();
requestAnimationFrame(frame);
