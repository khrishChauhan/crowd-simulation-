/* =========================================================================
   CrowdShield AI — Phase 5: Level Flow, Pre-Match Countdown & Victory Scorecard
   Arcade Strategy Duel: Human vs. AI Stadium Flow
   Complete Game Loop with Direct-Click Tactile Map Controls
   Zero External Build Steps — Vanilla JS + Canvas2D
   ========================================================================= */

(() => {
  "use strict";

  // ------------------------------------------------------------- Configuration
  const API_BASE = (window.CROWDSHIELD_API_BASE || `${location.protocol}//${location.hostname}:8000`);
  const WS_URL = API_BASE.replace(/^http/, "ws") + "/ws";

  const RISK_COLORS = {
    VERY_LOW: "#2ee59d",
    LOW: "#33d8ff",
    MEDIUM: "#f2c94c",
    HIGH: "#f7943a",
    CRITICAL: "#f04863"
  };

  const NODE_COLORS = {
    GATE: "#10b981",       // Green
    EXIT: "#ef4444",       // Red
    CHECKPOINT: "#60a5fa", // Cyan / Blue
    JUNCTION: "#a855f7",   // Purple
    SEATING: "#f59e0b",    // Amber
    EMERGENCY: "#ec4899"   // Pink
  };

  const LEVEL_CONFIGS = {
    1: { name: "ROOKIE", timeLimit: 120, target: 500, diff: "NOVICE" },
    2: { name: "PRO",    timeLimit: 120, target: 750, diff: "PRO" },
    3: { name: "MASTER", timeLimit: 100, target: 1000, diff: "PREDICTIVE" }
  };

  function riskBand(r) {
    if (r < 0.20) return "VERY_LOW";
    if (r < 0.40) return "LOW";
    if (r < 0.60) return "MEDIUM";
    if (r < 0.80) return "HIGH";
    return "CRITICAL";
  }

  function riskColor(r) {
    return RISK_COLORS[riskBand(r)] || "#33d8ff";
  }

  // ------------------------------------------------------------- State
  let latestPlayerState = null;
  let latestAiState = null;
  let activePopouts = []; // Floating hazard badges: { arena, text, gx, gy, startTime, duration }
  let activeAiBadges = []; // Floating AI decision badges: { text, gx, gy, startTime, duration }
  let lastAiDecisionKey = "";
  let ws = null;
  let wsConnected = false;
  let soundEnabled = true;

  // Game Loop State
  let currentLevel = 1;
  let isCountingDown = false;
  let countdownTimer = null;
  let matchStartTime = null;
  let matchEnded = false;
  let levelTimeLimitSec = 180;

  // Preloaded Visual Assets for Levels & Backgrounds
  const LEVEL_BACKGROUNDS = {
    1: "level1.png",
    2: "l2_bg.png",
    3: "map-bg.jpg"
  };
  const levelBgImg = new Image();
  levelBgImg.src = "/level1.png";
  const level1BgImg = levelBgImg;

  const boxSprite = new Image();
  boxSprite.src = "/box.png";

  const barrierSprite = new Image();
  barrierSprite.src = "/barrier.png";

  // Preloaded Countdown Graphics (3.png, 2.png, 1.png, fav.png)
  const COUNTDOWN_IMAGE_SRCS = ["3.png", "2.png", "1.png", "fav.png"];
  const preloadedCountdownImgs = {};
  COUNTDOWN_IMAGE_SRCS.forEach(src => {
    const img = new Image();
    img.src = "/" + src;
    preloadedCountdownImgs[src] = img;
  });

  // Preload Splash Screen Assets (1page.jpg, click.png)
  const splashBgImg = new Image();
  splashBgImg.src = "/1page.jpg";
  const clickPlayImg = new Image();
  clickPlayImg.src = "/click.png";

  // Tactile Map Hover State (Player Canvas Only)
  let hoveredNode = null;       // Reference to currently hovered node
  let hoveredCorridor = null;   // { u, v } where u < v
  let hoveredCrate = null;      // Crate obstacle hovered
  let hoveredBarrier = null;    // Rotatable diverter barrier hovered
  let toastTimer = null;

  // Tactical Interactive Objects Configuration (Dynamic from Admin Map API per Level)
  let playerCrates = {};
  let aiCrates = {};
  let playerBarriers = {};
  let aiBarriers = {};

  async function loadDynamicInteractiveObjects(lvl = 1) {
    playerCrates = {};
    aiCrates = {};
    playerBarriers = {};
    aiBarriers = {};

    try {
      const res = await fetch(`/api/admin/map/${lvl}`);
      if (res.ok) {
        const mapData = await res.json();
        if (mapData && mapData.backgroundImage) {
          levelBgImg.src = mapData.backgroundImage.startsWith("/") ? mapData.backgroundImage : "/" + mapData.backgroundImage;
        } else {
          levelBgImg.src = "/" + (LEVEL_BACKGROUNDS[lvl] || "level1.png");
        }
        if (mapData && mapData.custom !== false) {
          if (Array.isArray(mapData.crates)) {
            for (const c of mapData.crates) {
              playerCrates[c.id] = { id: c.id, name: c.name || c.id, x: c.x, y: c.y, edge: c.edge, active: false };
              aiCrates[c.id] = { id: c.id, name: c.name || c.id, x: c.x, y: c.y, edge: c.edge, active: false };
            }
          }
          if (Array.isArray(mapData.barriers)) {
            for (const b of mapData.barriers) {
              playerBarriers[b.id] = { id: b.id, name: b.name || b.id, x: b.x, y: b.y, angle: 0, straightEdge: b.straightEdge, divertEdge: b.divertEdge };
              aiBarriers[b.id] = { id: b.id, name: b.name || b.id, x: b.x, y: b.y, angle: 0, straightEdge: b.straightEdge, divertEdge: b.divertEdge };
            }
          }
          return;
        }
      }
      // If 404 or custom === false, fall back to hardcoded defaults
      levelBgImg.src = "/" + (LEVEL_BACKGROUNDS[lvl] || "level1.png");
      applyDefaultInteractiveObjects(lvl);
    } catch (err) {
      console.warn(`Could not load map for level ${lvl} from /api/admin/map/${lvl}, using defaults:`, err);
      levelBgImg.src = "/" + (LEVEL_BACKGROUNDS[lvl] || "level1.png");
      applyDefaultInteractiveObjects(lvl);
    }
  }

  function applyDefaultInteractiveObjects(lvl) {
    if (lvl === 1) {
      const defaultCrates = [
        { id: "BOX-01", name: "West Pass Crate", x: 270, y: 285, edge: ["H_L1", "B_L"] },
        { id: "BOX-02", name: "East Pass Crate", x: 975, y: 285, edge: ["H_R1", "B_R"] },
        { id: "BOX-03", name: "Center Altar Crate", x: 640, y: 200, edge: ["T_C", "C_MID"] },
      ];
      for (const c of defaultCrates) {
        playerCrates[c.id] = { ...c, active: false };
        aiCrates[c.id] = { ...c, active: false };
      }
      const defaultBarriers = [
        { id: "BARRIER-01", name: "West Diverter", x: 270, y: 390, straightEdge: ["B_L", "I_L1"], divertEdge: ["B_L", "O_L1"] },
        { id: "BARRIER-02", name: "East Diverter", x: 975, y: 390, straightEdge: ["B_R", "I_R1"], divertEdge: ["B_R", "O_R1"] },
      ];
      for (const b of defaultBarriers) {
        playerBarriers[b.id] = { ...b, angle: 0 };
        aiBarriers[b.id] = { ...b, angle: 0 };
      }
    }
  }

  // Canvases & Contexts
  let playerCanvas, playerCtx;
  let aiCanvas, aiCtx;

  // ------------------------------------------------------------- Audio FX (Web Audio API)
  let audioCtx = null;
  function getAudioCtx() {
    if (!audioCtx) {
      const AudioCtxClass = window.AudioContext || window.webkitAudioContext;
      if (AudioCtxClass) {
        audioCtx = new AudioCtxClass();
      }
    }
    if (audioCtx && audioCtx.state === "suspended") {
      audioCtx.resume().catch(() => {});
    }
    return audioCtx;
  }

  function playTone(freq, type, duration, gainVal = 0.12) {
    if (!soundEnabled) return;
    try {
      const ctx = getAudioCtx();
      if (!ctx) return;
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = type;
      osc.frequency.setValueAtTime(freq, ctx.currentTime);
      gain.gain.setValueAtTime(gainVal, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + duration);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start();
      osc.stop(ctx.currentTime + duration);
    } catch (e) {}
  }

  function playHazardSound() {
    if (!soundEnabled) return;
    playTone(750, "sawtooth", 0.1, 0.18);
    setTimeout(() => playTone(580, "sawtooth", 0.16, 0.18), 90);
  }

  function playClickSound() {
    if (!soundEnabled) return;
    playTone(560, "sine", 0.04, 0.08);
  }

  function playLockSound() {
    if (!soundEnabled) return;
    playTone(180, "sawtooth", 0.08, 0.2);
    setTimeout(() => playTone(120, "sawtooth", 0.1, 0.22), 60);
  }

  function playCountdownBeep() {
    if (!soundEnabled) return;
    playTone(320, "sine", 0.08, 0.2);
  }

  function playCountdownGo() {
    if (!soundEnabled) return;
    playTone(587.33, "triangle", 0.12, 0.22); // D5
    setTimeout(() => playTone(880, "triangle", 0.25, 0.28), 90); // A5
  }

  function playGateToggleSound(isBlocked) {
    if (!soundEnabled) return;
    if (isBlocked) {
      playTone(200, "sawtooth", 0.08, 0.22);
      setTimeout(() => playTone(120, "square", 0.12, 0.25), 45);
    } else {
      playTone(440, "sine", 0.06, 0.15);
      setTimeout(() => playTone(660, "triangle", 0.1, 0.18), 55);
    }
  }

  function playCorridorCycleSound(state) {
    if (!soundEnabled) return;
    if (state === "BLOCK") {
      playTone(170, "sawtooth", 0.12, 0.24);
    } else if (state === "FORWARD_ONLY" || state === "REVERSE_ONLY") {
      playTone(493, "triangle", 0.07, 0.14);
      setTimeout(() => playTone(587, "triangle", 0.09, 0.16), 50);
    } else {
      playTone(523, "sine", 0.07, 0.14);
      setTimeout(() => playTone(659, "sine", 0.1, 0.16), 60);
    }
  }

  function playWinSound() {
    if (!soundEnabled) return;
    // Celebratory victory fanfare
    [523.25, 659.25, 783.99, 1046.50].forEach((f, i) => {
      setTimeout(() => playTone(f, "triangle", 0.22, 0.22), i * 110);
    });
    setTimeout(() => {
      // Final triumphant chord
      playTone(523.25, "sine", 0.6, 0.15);
      playTone(659.25, "sine", 0.6, 0.15);
      playTone(1046.50, "triangle", 0.6, 0.22);
    }, 460);
  }

  function playDefeatSound() {
    if (!soundEnabled) return;
    // Minor descending defeat buzz
    [392, 349.23, 311.13, 261.63].forEach((f, i) => {
      setTimeout(() => playTone(f, "sawtooth", 0.2, 0.16), i * 120);
    });
  }

  // ------------------------------------------------------------- Networking
  async function apiPost(path, body) {
    try {
      const res = await fetch(API_BASE + path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body || {}),
      });
      return await res.json();
    } catch (e) {
      console.warn("API Post error:", path, e);
      showConnBanner(true);
      return null;
    }
  }

  async function apiGet(path) {
    try {
      const res = await fetch(API_BASE + path);
      return await res.json();
    } catch (e) {
      showConnBanner(true);
      return null;
    }
  }

  function showConnBanner(show) {
    const el = document.getElementById("connBanner");
    const chip = document.getElementById("chipWs");
    if (el) {
      document.getElementById("connUrl").textContent = API_BASE;
      el.hidden = !show;
    }
    if (chip) {
      const dot = chip.querySelector(".dot");
      if (dot) {
        dot.className = show ? "dot dot-red" : "dot dot-green";
      }
      const lastChild = chip.childNodes[chip.childNodes.length - 1];
      if (lastChild && lastChild.nodeType === 3) {
        lastChild.nodeValue = show ? " RECONNECTING..." : " LIVE DUAL SYNC";
      }
    }
  }

  function showSafetyToast(msg) {
    const toast = document.getElementById("safetyToast");
    const msgEl = document.getElementById("safetyToastMsg");
    if (!toast) return;
    if (msgEl) msgEl.textContent = msg || "CANNOT DISCONNECT ALL EXITS — EVACUATION ROUTE REQUIRED";
    toast.hidden = false;
    toast.style.animation = "none";
    void toast.offsetWidth; // force DOM reflow
    toast.style.animation = "toastShake 0.45s ease-in-out";
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { toast.hidden = true; }, 2600);
  }

  function connectWS() {
    try {
      ws = new WebSocket(WS_URL);
    } catch (e) {
      scheduleReconnect();
      return;
    }

    ws.onopen = () => {
      wsConnected = true;
      showConnBanner(false);
    };

    ws.onclose = () => {
      wsConnected = false;
      scheduleReconnect();
    };

    ws.onerror = () => {
      wsConnected = false;
    };

    ws.onmessage = (msg) => {
      let data;
      try {
        data = JSON.parse(msg.data);
      } catch (e) {
        return;
      }

      if (data.type === "game_state") {
        if (data.player) latestPlayerState = data.player;
        if (data.ai) latestAiState = data.ai;
        updateRaceHud();
      } else if (data.type === "crowd_state") {
        if (data.arena_id === "ai") {
          latestAiState = data;
        } else {
          latestPlayerState = data;
        }
        updateRaceHud();
      } else if (data.type === "HAZARD_INCIDENT" || (data.type === "event" && (data.message || "").includes("CRUSH"))) {
        handleHazardEvent(data);
      }
    };
  }

  function scheduleReconnect() {
    showConnBanner(true);
    setTimeout(connectWS, 2500);
  }

  // ------------------------------------------------------------- Hazard Events
  function handleHazardEvent(evt) {
    const arenaId = evt.arena_id || "player";
    const payload = evt.payload || {};
    const nodeId = payload.node || "CP-02";

    // Trigger perimeter red flash
    const flashEl = document.getElementById(arenaId === "player" ? "playerFlash" : "aiFlash");
    if (flashEl) {
      flashEl.classList.remove("hazard-active");
      void flashEl.offsetWidth; // force DOM reflow
      flashEl.classList.add("hazard-active");
      setTimeout(() => flashEl.classList.remove("hazard-active"), 1000);
    }

    // Locate node coordinates for floating stamp
    const state = arenaId === "player" ? latestPlayerState : latestAiState;
    let gx = 500, gy = 350;
    if (state && state.graph && state.graph.nodes) {
      const node = state.graph.nodes.find(n => n.id === nodeId);
      if (node) {
        gx = node.x;
        gy = node.y;
      }
    }

    activePopouts.push({
      arena: arenaId,
      nodeId: nodeId,
      text: `⚠ CRUSH ALERT! +3s`,
      gx: gx,
      gy: gy,
      startTime: performance.now(),
      duration: 1800,
    });

    playHazardSound();
  }

  function updateCountdownGraphic(imgEl, src, isGo = false) {
    if (!imgEl) return;
    imgEl.src = "/" + src;
    if (isGo) {
      imgEl.classList.add("countdown-go-img");
    } else {
      imgEl.classList.remove("countdown-go-img");
    }
    // Re-trigger the CSS entrance scale animation on every image swap
    imgEl.classList.remove("countdown-tick-anim");
    void imgEl.offsetWidth; // Force reflow
    imgEl.classList.add("countdown-tick-anim");
  }

  // ------------------------------------------------------------- Pre-Match Countdown Sequence
  function startPreMatchCountdown(onComplete) {
    const overlay = document.getElementById("countdownOverlay");
    const imgEl = document.getElementById("countdownImg");
    const numEl = document.getElementById("countdownText");
    const subEl = document.getElementById("countdownSub");
    if (!overlay) {
      if (onComplete) onComplete();
      return;
    }

    isCountingDown = true;
    overlay.hidden = false;
    if (numEl) {
      numEl.classList.remove("countdown-go");
      numEl.textContent = "3";
    }
    if (subEl) {
      subEl.classList.remove("subhead-go");
      subEl.textContent = "PREPARE STADIUM FLOW";
    }
    updateCountdownGraphic(imgEl, "3.png", false);
    playCountdownBeep();

    if (countdownTimer) clearTimeout(countdownTimer);

    // Step 2: 2
    countdownTimer = setTimeout(() => {
      if (numEl) numEl.textContent = "2";
      if (subEl) subEl.textContent = "MONITOR BOTTLENECKS";
      updateCountdownGraphic(imgEl, "2.png", false);
      playCountdownBeep();

      // Step 3: 1
      countdownTimer = setTimeout(() => {
        if (numEl) numEl.textContent = "1";
        if (subEl) subEl.textContent = "READY GATES & CORRIDORS";
        updateCountdownGraphic(imgEl, "1.png", false);
        playCountdownBeep();

        // Step 4: Final Tick (Match Start) -> fav.png / CONTROL THE FLOW!
        countdownTimer = setTimeout(() => {
          if (numEl) {
            numEl.textContent = "CONTROL THE FLOW!";
            numEl.classList.add("countdown-go");
          }
          if (subEl) {
            subEl.textContent = "CONTROL THE FLOW!";
            subEl.classList.add("subhead-go");
          }
          updateCountdownGraphic(imgEl, "fav.png", true);
          playCountdownGo();

          // Step 5: Start match physics after 500ms
          countdownTimer = setTimeout(() => {
            overlay.hidden = true;
            if (numEl) numEl.classList.remove("countdown-go");
            if (subEl) subEl.classList.remove("subhead-go");
            if (imgEl) imgEl.classList.remove("countdown-go-img");
            isCountingDown = false;
            matchStartTime = performance.now();
            matchEnded = false;
            apiPost("/api/simulation/start");
            const leadLabel = document.getElementById("raceLeadStatus");
            if (leadLabel) leadLabel.textContent = "DUEL IN PROGRESS";
            triggerStartHint();
            if (onComplete) onComplete();
          }, 500);
        }, 1000);
      }, 1000);
    }, 1000);
  }

  // Alias for external callers
  const startCountdown = startPreMatchCountdown;

  function triggerStartHint() {
    const hint = document.getElementById("startHint");
    if (!hint) return;
    hint.hidden = false;
    hint.classList.remove("fade-out");
    setTimeout(() => {
      hint.classList.add("fade-out");
      setTimeout(() => { hint.hidden = true; }, 1200);
    }, 4000);
  }

  // ------------------------------------------------------------- Level Loading & Campaign Flow
  async function loadLevel(lvl) {
    currentLevel = lvl;
    matchEnded = false;
    isCountingDown = true;
    matchStartTime = null;
    activePopouts = [];
    activeAiBadges = [];
    lastAiDecisionKey = "";

    // Dynamically load interactive objects from Admin Map API
    await loadDynamicInteractiveObjects(lvl);

    // Reset Level 1 tactical props
    for (const k in playerCrates) playerCrates[k].active = false;
    for (const k in aiCrates) aiCrates[k].active = false;
    for (const k in playerBarriers) playerBarriers[k].angle = 0;
    for (const k in aiBarriers) aiBarriers[k].angle = 0;
    hoveredCrate = null;
    hoveredBarrier = null;

    // Reset pause state on level reload
    const btnPause = document.getElementById("btnPause");
    if (btnPause) {
      btnPause.classList.remove("paused");
      btnPause.title = "Pause Match";
      const iconPause = btnPause.querySelector(".icon-pause");
      const iconPlay = btnPause.querySelector(".icon-play");
      if (iconPause) iconPause.style.display = "";
      if (iconPlay) iconPlay.style.display = "none";
    }

    // Hide Scorecard if open
    const modal = document.getElementById("scorecardModal");
    if (modal) modal.hidden = true;

    // Update active level pill & menu
    [1, 2, 3].forEach(l => {
      const btn = document.getElementById(`btnLvl${l}`);
      if (btn) btn.classList.toggle("active", l === lvl);
    });
    const activeLevelLabel = document.getElementById("activeLevelLabel");
    if (activeLevelLabel) activeLevelLabel.textContent = `LVL ${lvl}`;
    const levelMenu = document.getElementById("levelMenu");
    if (levelMenu) levelMenu.hidden = true;

    const cfg = LEVEL_CONFIGS[lvl] || LEVEL_CONFIGS[1];
    levelTimeLimitSec = cfg.timeLimit;

    // Reset local state counters immediately so HUD reflects target
    if (latestPlayerState) {
      latestPlayerState.evacuated_count = 0;
      latestPlayerState.target_evacuation = cfg.target;
      if (latestPlayerState.metrics) latestPlayerState.metrics.target_evacuation = cfg.target;
      latestPlayerState.penalty_seconds = 0;
      latestPlayerState.incident_count = 0;
      latestPlayerState.sim_time = 0;
    }
    if (latestAiState) {
      latestAiState.evacuated_count = 0;
      latestAiState.target_evacuation = cfg.target;
      if (latestAiState.metrics) latestAiState.metrics.target_evacuation = cfg.target;
      latestAiState.difficulty = cfg.diff.toLowerCase();
      latestAiState.penalty_seconds = 0;
      latestAiState.incident_count = 0;
      latestAiState.sim_time = 0;
    }

    // Reset HUD timer display immediately
    const mins = Math.floor(levelTimeLimitSec / 60).toString().padStart(2, "0");
    const secs = Math.floor(levelTimeLimitSec % 60).toString().padStart(2, "0");
    const timerEl = document.getElementById("matchTimer");
    if (timerEl) timerEl.textContent = `${mins}:${secs}`;

    const leadLabel = document.getElementById("raceLeadStatus");
    if (leadLabel) {
      leadLabel.textContent = `LEVEL ${lvl}: ${cfg.name}`;
      leadLabel.style.color = "var(--text-dim)";
    }
    updateRaceHud();

    // Call backend to configure level, difficulty, and cleanly reset both arenas (paused during countdown)
    await apiPost("/api/game/level", {
      level: lvl,
      start_immediately: false,
    });

    // Start 3-2-1 countdown sequence
    startPreMatchCountdown();
  }

  let aiToastTimeout = null;
  function showAiFloatingToast(msg) {
    const toast = document.getElementById("aiToast");
    const textEl = document.getElementById("aiToastText");
    if (!toast || !textEl) return;
    textEl.textContent = msg;
    toast.hidden = false;
    toast.classList.remove("fade-out");
    if (aiToastTimeout) clearTimeout(aiToastTimeout);
    aiToastTimeout = setTimeout(() => {
      toast.classList.add("fade-out");
      setTimeout(() => { toast.hidden = true; }, 300);
    }, 2800);
  }

  // ------------------------------------------------------------- Race HUD Updater & Win/Loss Evaluator
  function updateRaceHud() {
    if (!latestPlayerState && !latestAiState) return;

    const pState = latestPlayerState || { metrics: {}, evacuated_count: 0, target_evacuation: 500, penalty_seconds: 0, sim_time: 0 };
    const aState = latestAiState || { metrics: {}, evacuated_count: 0, target_evacuation: 500, penalty_seconds: 0, sim_time: 0 };

    const pEvac = pState.evacuated_count ?? pState.metrics?.evacuated_count ?? 0;
    const pTarget = pState.target_evacuation ?? pState.metrics?.target_evacuation ?? LEVEL_CONFIGS[currentLevel]?.target ?? 500;
    const pPct = Math.min(100, Math.round((pEvac / Math.max(1, pTarget)) * 100));

    const aEvac = aState.evacuated_count ?? aState.metrics?.evacuated_count ?? 0;
    const aTarget = aState.target_evacuation ?? aState.metrics?.target_evacuation ?? LEVEL_CONFIGS[currentLevel]?.target ?? 500;
    const aPct = Math.min(100, Math.round((aEvac / Math.max(1, aTarget)) * 100));

    // Player HUD
    const pEvacEl = document.getElementById("playerEvacText");
    const pPctEl = document.getElementById("playerPctText");
    const pBarEl = document.getElementById("playerProgressBar");
    if (pEvacEl) pEvacEl.textContent = `${pEvac} / ${pTarget}`;
    if (pPctEl) pPctEl.textContent = `${pPct}%`;
    if (pBarEl) pBarEl.style.width = `${pPct}%`;

    // AI HUD
    const aiDiff = (aState.difficulty || LEVEL_CONFIGS[currentLevel]?.diff || "novice").toUpperCase();
    const aiBadgeEl = document.getElementById("aiDiffBadge");
    const aiEvacEl = document.getElementById("aiEvacText");
    const aiPctEl = document.getElementById("aiPctText");
    const aiBarEl = document.getElementById("aiProgressBar");
    if (aiBadgeEl) aiBadgeEl.textContent = `AI: ${aiDiff}`;
    if (aiEvacEl) aiEvacEl.textContent = `${aEvac} / ${aTarget}`;
    if (aiPctEl) aiPctEl.textContent = `${aPct}%`;
    if (aiBarEl) aiBarEl.style.width = `${aPct}%`;

    // Penalties & Danger Indicator Calculation
    const pPen = Math.round(pState.penalty_seconds ?? pState.metrics?.penalty_seconds ?? 0);
    const aPen = Math.round(aState.penalty_seconds ?? aState.metrics?.penalty_seconds ?? 0);
    const penEl = document.getElementById("penaltyDisplay");
    if (penEl) penEl.textContent = `+${pPen}s / +${aPen}s PENALTY`;

    let maxRisk = 0;
    if (pState.graph && pState.graph.nodes) {
      for (const n of pState.graph.nodes) {
        if ((n.risk || 0) > maxRisk) maxRisk = n.risk;
      }
    }
    const dangerChip = document.getElementById("dangerChip");
    const dangerText = document.getElementById("dangerStatusText");
    if (dangerChip && dangerText) {
      if (maxRisk >= 0.75 || (pState.incident_count > 0 && pPen > 0)) {
        dangerChip.className = "hud-chip danger-chip critical";
        dangerText.textContent = "CRITICAL";
      } else if (maxRisk >= 0.45) {
        dangerChip.className = "hud-chip danger-chip elevated";
        dangerText.textContent = "DANGER";
      } else {
        dangerChip.className = "hud-chip danger-chip safe";
        dangerText.textContent = "SAFE";
      }
    }

    // Match Countdown Timer (Counts down from level limit towards 00:00)
    let timeRemaining = levelTimeLimitSec;
    let baseElapsedSec = 0;
    if (matchStartTime && !isCountingDown) {
      baseElapsedSec = (performance.now() - matchStartTime) / 1000;
      timeRemaining = Math.max(0, levelTimeLimitSec - baseElapsedSec);
    }
    const mins = Math.floor(timeRemaining / 60).toString().padStart(2, "0");
    const secs = Math.floor(timeRemaining % 60).toString().padStart(2, "0");
    const timerEl = document.getElementById("matchTimer");
    if (timerEl) timerEl.textContent = `${mins}:${secs}`;

    // Tug-of-War Bar (Leader display)
    const totalEvac = pEvac + aEvac;
    let pHalfrate = 50;
    if (totalEvac > 0) {
      pHalfrate = Math.min(94, Math.max(6, Math.round((pEvac / totalEvac) * 100)));
    }
    const tugHuman = document.getElementById("tugHuman");
    const tugAi = document.getElementById("tugAi");
    if (tugHuman) tugHuman.style.width = `${pHalfrate}%`;
    if (tugAi) tugAi.style.width = `${100 - pHalfrate}%`;

    // Race Lead Label
    const leadLabel = document.getElementById("raceLeadStatus");
    if (leadLabel && !matchEnded && !isCountingDown) {
      if (pEvac > aEvac) {
        leadLabel.textContent = `HUMAN LEADING +${pEvac - aEvac}`;
        leadLabel.style.color = "var(--accent-emerald)";
      } else if (aEvac > pEvac) {
        leadLabel.textContent = `AI LEADING +${aEvac - pEvac}`;
        leadLabel.style.color = "var(--accent-purple)";
      } else {
        leadLabel.textContent = totalEvac > 0 ? "TIED RACE" : "DUEL IN PROGRESS";
        leadLabel.style.color = "var(--text-dim)";
      }
    }

    // Left Arena Card Indicators (for test-compat)
    const pLive = Math.round(pState.metrics?.total_crowd || 0);
    const pCrowdCount = document.getElementById("playerCrowdCount");
    const pRiskVal = document.getElementById("playerRiskVal");
    const pPenaltyVal = document.getElementById("playerPenaltyVal");
    const pCrushVal = document.getElementById("playerCrushVal");
    if (pCrowdCount) pCrowdCount.textContent = `${pLive} AGENTS LIVE`;
    if (pRiskVal) pRiskVal.textContent = (pState.metrics?.network_risk ?? 0).toFixed(2);
    if (pPenaltyVal) pPenaltyVal.textContent = `+${pPen}s`;
    if (pCrushVal) pCrushVal.textContent = pState.incident_count ?? pState.metrics?.incident_count ?? 0;

    // Right Arena Card Indicators (for test-compat)
    const aLive = Math.round(aState.metrics?.total_crowd || 0);
    const aCrowdCount = document.getElementById("aiCrowdCount");
    const aRiskVal = document.getElementById("aiRiskVal");
    const aPenaltyVal = document.getElementById("aiPenaltyVal");
    const aCrushVal = document.getElementById("aiCrushVal");
    if (aCrowdCount) aCrowdCount.textContent = `${aLive} AGENTS LIVE`;
    if (aRiskVal) aRiskVal.textContent = (aState.metrics?.network_risk ?? 0).toFixed(2);
    if (aPenaltyVal) aPenaltyVal.textContent = `+${aPen}s`;
    if (aCrushVal) aCrushVal.textContent = aState.incident_count ?? aState.metrics?.incident_count ?? 0;

    // Check for AI Actions and show floating notification
    checkAiDecisionBadges(aState);

  // ------------------------------------------------------------- AI Decision Badges
  function checkAiDecisionBadges(aiState) {
    if (!aiState || !aiState.active_decision) return;
    const dec = aiState.active_decision;
    if (!dec || !dec.action || dec.action === "DO_NOTHING") return;
    const loc = dec.intervention_location || dec.risk_location || "CENTRAL";
    const decKey = `${dec.action}_${loc}_${dec.duration_sec || 0}_${aiState.tick || 0}`;
    if (decKey === lastAiDecisionKey) return;
    lastAiDecisionKey = decKey;

    let badgeText = `🤖 AI: ${dec.action.replace(/_/g, " ")}`;
    if (dec.action === "REDIRECT_LEFT") badgeText = "🤖 AI: REDIRECT LEFT ◀";
    else if (dec.action === "REDIRECT_RIGHT") badgeText = "🤖 AI: REDIRECT RIGHT ▶";
    else if (dec.action === "HOLD") badgeText = "🤖 AI: HOLD GATE ⏸";
    else if (dec.action === "BLOCK_EDGE") badgeText = "🤖 AI: SEAL CORRIDOR ✕";
    else if (dec.action === "SPLIT_FLOW") badgeText = "🤖 AI: SPLIT CONCOURSE ⑂";

    const reasonLower = (dec.reason || "").toLowerCase();
    if (reasonLower.includes("exit")) {
      if (dec.action === "REDIRECT_LEFT") badgeText = "🤖 AI: DIVERT TO EXIT 1 ◀";
      else if (dec.action === "REDIRECT_RIGHT") badgeText = "🤖 AI: DIVERT TO EXIT 3 ▶";
      else badgeText = "🤖 AI: BALANCE EXITS ⚖";
    }

    if ((dec.reason || "").includes("+30s") || reasonLower.includes("anticipat") || reasonLower.includes("proactive")) {
      badgeText = "🤖 AI: PROACTIVE REROUTE (+30s) ⚡";
    }

    let gx = 500, gy = 350;
    if (aiState.graph && aiState.graph.nodes) {
      const node = aiState.graph.nodes.find(n => n.id === loc) || aiState.graph.nodes.find(n => n.id === dec.risk_location);
      if (node) {
        gx = node.x;
        gy = node.y;
      }
    }

    activeAiBadges.push({
      text: badgeText,
      gx: gx,
      gy: gy,
      startTime: performance.now(),
      duration: 2500,
    });

    let cleanNotice = `AI: ${dec.action.replace(/_/g, " ")}`;
    if (dec.action === "REDIRECT_LEFT") cleanNotice = "AI diverted flow to Exit 1";
    else if (dec.action === "REDIRECT_RIGHT") cleanNotice = "AI diverted flow to Exit 3";
    else if (dec.action === "HOLD") cleanNotice = `AI held gate at ${loc}`;
    else if (dec.action === "BLOCK_EDGE") cleanNotice = "AI sealed corridor";
    if (reasonLower.includes("anticipat") || reasonLower.includes("proactive")) {
      cleanNotice = "AI proactive reroute (+30s)";
    }
    showAiFloatingToast(cleanNotice);
  }

    // AI Decision Text Pill & Live Thought Ticker
    const decPill = document.getElementById("aiDecisionText");
    const decReason = document.getElementById("aiReasonInfo");
    if (decPill && decReason) {
      checkAiDecisionBadges(aState);
      const dec = aState.active_decision;
      const isIntervening = dec && dec.action && dec.action !== "DO_NOTHING";
      if (currentLevel === 1) {
        decPill.textContent = isIntervening ? `AI (NOVICE): ${dec.action.replace(/_/g, " ")}` : "AI (NOVICE): OBSERVATION";
        decReason.textContent = isIntervening ? `AI (Novice): "${dec.reason || "Holding gate to damp incoming crowd surge."}"` : 'AI (Novice): "Waiting for bottleneck to form..."';
      } else if (currentLevel === 2) {
        decPill.textContent = isIntervening ? `AI (PRO): ${dec.action.replace(/_/g, " ")}` : "AI (PRO): FLOW OPTIMIZER";
        decReason.textContent = isIntervening ? `AI (Pro): "${dec.reason || "Balancing concourse flow between Exit 1 and Exit 2."}"` : 'AI (Pro): "Balancing concourse flow between Exit 1 and Exit 2."';
      } else {
        decPill.textContent = isIntervening ? `AI (MASTER): ${dec.action.replace(/_/g, " ")} (+30s)` : "AI (MASTER): PREDICTIVE FLOW";
        decReason.textContent = isIntervening ? `AI (Master): "⚡ ${dec.reason || "Pre-emptively diverting flow before bottleneck."}"` : 'AI (Master): "Predicting crush at Checkpoint 4 in +30s -> Pre-emptively diverting flow."';
      }
    }

    // -----------------------------------------------------------
    // WIN / LOSS EVALUATION
    // -----------------------------------------------------------
    if (!matchEnded && !isCountingDown && matchStartTime) {
      // 1. Human Victory: Player hit target first
      if (pEvac >= pTarget && aEvac < pTarget) {
        matchEnded = true;
        apiPost("/api/simulation/stop");
        if (leadLabel) {
          leadLabel.textContent = "🏆 HUMAN VICTORY!";
          leadLabel.style.color = "var(--accent-cyan)";
        }
        playWinSound();
        showScorecard("VICTORY", baseElapsedSec, pState, aState);
      }
      // 2. AI Victory: AI hit target first
      else if (aEvac >= aTarget && pEvac < aTarget) {
        matchEnded = true;
        apiPost("/api/simulation/stop");
        if (leadLabel) {
          leadLabel.textContent = "🤖 AI OUTPERFORMS!";
          leadLabel.style.color = "var(--accent-violet)";
        }
        playDefeatSound();
        showScorecard("DEFEAT_AI", baseElapsedSec, pState, aState);
      }
      // 3. Time Expired Defeat
      else if (timeRemaining <= 0 && pEvac < pTarget) {
        matchEnded = true;
        apiPost("/api/simulation/stop");
        if (leadLabel) {
          leadLabel.textContent = "⏱ TIME EXPIRED!";
          leadLabel.style.color = "var(--risk-critical)";
        }
        playDefeatSound();
        showScorecard("DEFEAT_TIME", baseElapsedSec, pState, aState);
      }
    }
  }

  // ------------------------------------------------------------- Scorecard Modal
  function formatMMSS(totalSec) {
    const s = Math.max(0, Math.round(totalSec));
    const m = Math.floor(s / 60).toString().padStart(2, "0");
    const sec = Math.floor(s % 60).toString().padStart(2, "0");
    return `${m}:${sec}`;
  }

  function calcStars(incidents) {
    if (incidents === 0) return "⭐⭐⭐⭐⭐";
    if (incidents <= 2) return "⭐⭐⭐⭐☆";
    if (incidents <= 4) return "⭐⭐⭐☆☆";
    return "⭐⭐☆☆☆";
  }

  function showScorecard(result = "VICTORY", baseElapsed = 45, pState = latestPlayerState || {}, aState = latestAiState || {}) {
    if (!pState) pState = {};
    if (!aState) aState = {};
    const modal = document.getElementById("scorecardModal");
    const banner = document.getElementById("scorecardBanner");
    const trophy = document.getElementById("scorecardTrophy");
    const headline = document.getElementById("scorecardHeadline");
    const subhead = document.getElementById("scorecardSubhead");
    const btnNext = document.getElementById("btnNextLevel");

    if (!modal) return;

    // Header Banner
    if (result === "VICTORY") {
      banner.classList.remove("banner-defeat");
      trophy.textContent = "🏆";
      headline.textContent = "🏆 HUMAN VICTORY";
      subhead.textContent = "YOU EVACUATED FASTER THAN THE AI!";
      
      // Unlock next level progression
      const nextLvl = currentLevel + 1;
      if (nextLvl > maxUnlockedLevel && nextLvl <= 3) {
        maxUnlockedLevel = nextLvl;
        try {
          localStorage.setItem("crowdshield_max_unlocked", maxUnlockedLevel.toString());
        } catch (e) {}
        updateMapButtons();
      }

      if (btnNext) {
        btnNext.disabled = currentLevel >= 3;
        btnNext.textContent = currentLevel >= 3 ? "CAMPAIGN CLEAR! 🏆" : "NEXT LEVEL ▶";
      }
    } else if (result === "DEFEAT_AI") {
      banner.classList.add("banner-defeat");
      trophy.textContent = "🤖";
      headline.textContent = "🤖 AI WINS";
      subhead.textContent = "MACHINE OPTIMIZATION OUTPACED HUMAN FLOW!";
      if (btnNext) {
        btnNext.disabled = true;
        btnNext.textContent = "NEXT LEVEL ▶";
      }
    } else {
      // DEFEAT_TIME
      banner.classList.add("banner-defeat");
      trophy.textContent = "⏱";
      headline.textContent = "⏱ TIME EXPIRED";
      subhead.textContent = "STADIUM EVACUATION INCOMPLETE!";
      if (btnNext) {
        btnNext.disabled = true;
        btnNext.textContent = "NEXT LEVEL ▶";
      }
    }

    // Human Column
    const pEvac = pState.evacuated_count ?? pState.metrics?.evacuated_count ?? 0;
    const pTarget = pState.target_evacuation ?? pState.metrics?.target_evacuation ?? LEVEL_CONFIGS[currentLevel]?.target ?? 500;
    const pPen = Math.round(pState.penalty_seconds ?? pState.metrics?.penalty_seconds ?? 0);
    const pHazards = pState.incident_count ?? pState.metrics?.incident_count ?? 0;
    const pFinalTime = baseElapsed + pPen;

    document.getElementById("scPlayerEvac").textContent = `${pEvac} / ${pTarget}`;
    document.getElementById("scPlayerBaseTime").textContent = formatMMSS(baseElapsed);
    document.getElementById("scPlayerHazards").textContent = `${pHazards} (+${pPen}s penalty)`;
    document.getElementById("scPlayerFinalTime").textContent = formatMMSS(pFinalTime);
    document.getElementById("scPlayerStars").textContent = calcStars(pHazards);

    // AI Column
    const aEvac = aState.evacuated_count ?? aState.metrics?.evacuated_count ?? 0;
    const aTarget = aState.target_evacuation ?? aState.metrics?.target_evacuation ?? LEVEL_CONFIGS[currentLevel]?.target ?? 500;
    const aPen = Math.round(aState.penalty_seconds ?? aState.metrics?.penalty_seconds ?? 0);
    const aHazards = aState.incident_count ?? aState.metrics?.incident_count ?? 0;
    const aFinalTime = baseElapsed + aPen;
    const aiDiff = (aState.difficulty || LEVEL_CONFIGS[currentLevel]?.diff || "novice").toUpperCase();

    document.getElementById("scAiBadge").textContent = `AI: ${aiDiff}`;
    document.getElementById("scAiEvac").textContent = `${aEvac} / ${aTarget}`;
    document.getElementById("scAiBaseTime").textContent = formatMMSS(baseElapsed);
    document.getElementById("scAiHazards").textContent = `${aHazards} (+${aPen}s penalty)`;
    document.getElementById("scAiFinalTime").textContent = formatMMSS(aFinalTime);
    document.getElementById("scAiStars").textContent = calcStars(aHazards);

    modal.hidden = false;
  }
  window.showScorecard = showScorecard;

  // ------------------------------------------------------------- Geometry & Hit Testing
  function pointToSegmentDistance(px, py, x1, y1, x2, y2) {
    const dx = x2 - x1;
    const dy = y2 - y1;
    const lenSq = dx * dx + dy * dy;
    if (lenSq === 0) return Math.hypot(px - x1, py - y1);
    const t = Math.max(0, Math.min(1, ((px - x1) * dx + (py - y1) * dy) / lenSq));
    const projX = x1 + t * dx;
    const projY = y1 + t * dy;
    return Math.hypot(px - projX, py - projY);
  }

  function getCorridorState(state, u, v) {
    if (!state || !state.graph || !state.graph.edges) return "BIDIRECTIONAL";
    const e1 = state.graph.edges.find(e => e.source === u && e.target === v);
    const e2 = state.graph.edges.find(e => e.source === v && e.target === u);
    const e1Blocked = !e1 || !e1.enabled || e1.control_state === "BLOCK" || e1.direction === "BLOCK";
    const e2Blocked = !e2 || !e2.enabled || e2.control_state === "BLOCK" || e2.direction === "BLOCK";
    if (e1Blocked && e2Blocked) return "BLOCK";
    if (!e1Blocked && e2Blocked) return "FORWARD_ONLY"; // u -> v
    if (e1Blocked && !e2Blocked) return "REVERSE_ONLY"; // v -> u
    return "BIDIRECTIONAL";
  }

  // ------------------------------------------------------------- Canvas Setup & Coordinates
  function initCanvases() {
    playerCanvas = document.getElementById("playerCanvas");
    playerCtx = playerCanvas ? playerCanvas.getContext("2d") : null;
    aiCanvas = document.getElementById("aiCanvas");
    aiCtx = aiCanvas ? aiCanvas.getContext("2d") : null;

    window.addEventListener("resize", () => {
      if (playerCanvas) fitCanvas(playerCanvas);
      if (aiCanvas) fitCanvas(aiCanvas);
    });

    if (playerCanvas) fitCanvas(playerCanvas);
    if (aiCanvas) fitCanvas(aiCanvas);

    // Interactive Canvas ONLY for Player (aiCanvas remains purely spectator)
    if (playerCanvas) {
      playerCanvas.addEventListener("mousemove", onPlayerMouseMove);
      playerCanvas.addEventListener("mouseleave", onPlayerMouseLeave);
      playerCanvas.addEventListener("click", onPlayerCanvasClick);
    }
  }

  function fitCanvas(canvas) {
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.parentElement ? canvas.parentElement.getBoundingClientRect() : { width: 600, height: 400 };
    const w = Math.max(100, Math.floor(rect.width * dpr));
    const h = Math.max(100, Math.floor(rect.height * dpr));
    if (canvas.width !== w || canvas.height !== h) {
      canvas.width = w;
      canvas.height = h;
    }
  }

  function graphToScreen(gx, gy, canvasWidth, canvasHeight) {
    const isImageBg = currentLevel === 1 || currentLevel === 2 || (levelBgImg.complete && levelBgImg.naturalWidth > 0 && currentLevel !== 3);
    const gw = isImageBg ? 1280 : 1000;
    const gh = isImageBg ? 853 : 700;
    const margin = isImageBg ? 8 : 45;
    const scale = Math.min((canvasWidth - margin * 2) / gw, (canvasHeight - margin * 2) / gh);
    const offsetX = (canvasWidth - gw * scale) / 2;
    const offsetY = (canvasHeight - gh * scale) / 2;
    return [offsetX + gx * scale, offsetY + gy * scale, scale];
  }

  // ------------------------------------------------------------- Tactile Map Hover & Click Handlers
  function onPlayerMouseMove(ev) {
    if (!latestPlayerState || !latestPlayerState.graph) return;
    const rect = playerCanvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    const mx = (ev.clientX - rect.left) * dpr;
    const my = (ev.clientY - rect.top) * dpr;
    const w = playerCanvas.width, h = playerCanvas.height;

    // 0. Tactical Interactive Objects Hit Test (Crates & Barriers)
    if (currentLevel === 1 || Object.keys(playerCrates).length > 0 || Object.keys(playerBarriers).length > 0) {
      // A. Crate Obstacles Hit Test
      for (const cid in playerCrates) {
        const crate = playerCrates[cid];
        const [cx, cy, s] = graphToScreen(crate.x, crate.y, w, h);
        const radius = Math.max(22 * dpr, 24 * s);
        if (Math.hypot(mx - cx, my - cy) <= radius) {
          hoveredCrate = crate;
          hoveredBarrier = null;
          hoveredNode = null;
          hoveredCorridor = null;
          playerCanvas.style.cursor = "pointer";

          const gameTooltip = document.getElementById("gameTooltip");
          if (gameTooltip) {
            gameTooltip.textContent = crate.active ? "Click to Remove Wooden Crate" : "Click to Drop Crate (Block Route)";
            gameTooltip.style.left = `${ev.clientX - rect.left + 14}px`;
            gameTooltip.style.top = `${ev.clientY - rect.top + 14}px`;
            gameTooltip.hidden = false;
          }
          const info = document.getElementById("playerInspectInfo");
          if (info) {
            info.textContent = `${crate.name}: ${crate.active ? "BLOCKED BY CRATE (Click to Clear)" : "OPEN (Click to Place Crate)"}`;
          }
          return;
        }
      }

      // B. Rotatable Barriers Hit Test
      for (const bid in playerBarriers) {
        const barrier = playerBarriers[bid];
        const [bx, by, s] = graphToScreen(barrier.x, barrier.y, w, h);
        const radius = Math.max(24 * dpr, 26 * s);
        if (Math.hypot(mx - bx, my - by) <= radius) {
          hoveredBarrier = barrier;
          hoveredCrate = null;
          hoveredNode = null;
          hoveredCorridor = null;
          playerCanvas.style.cursor = "pointer";

          const gameTooltip = document.getElementById("gameTooltip");
          if (gameTooltip) {
            gameTooltip.textContent = barrier.angle === 0 ? "Click to Rotate Barrier 90° (Divert Flow)" : "Click to Rotate 0° (Restore Straight Flow)";
            gameTooltip.style.left = `${ev.clientX - rect.left + 14}px`;
            gameTooltip.style.top = `${ev.clientY - rect.top + 14}px`;
            gameTooltip.hidden = false;
          }
          const info = document.getElementById("playerInspectInfo");
          if (info) {
            info.textContent = `${barrier.name}: ${barrier.angle === 0 ? "STRAIGHT (0°) - Direct Flow" : "ROTATED (90°) - Diverted Flow"}`;
          }
          return;
        }
      }
    }
    hoveredCrate = null;
    hoveredBarrier = null;

    // 1. Node Hit Test (maintains click precision with 10px-12px padding around the smaller visual circle)
    let hitNode = null;
    for (const node of latestPlayerState.graph.nodes) {
      if (node.id === "EMG-01" && !latestPlayerState.emergency_active) continue;
      const [nx, ny, s] = graphToScreen(node.x, node.y, w, h);
      const isGate = node.type === "GATE";
      const isExit = node.type === "EXIT";
      const visualR = (isGate || isExit ? 6.0 : 4.5) * s;
      const hitRadius = visualR + Math.max(10 * dpr, 11 * s);
      if (Math.hypot(mx - nx, my - ny) <= hitRadius) {
        hitNode = node;
        break;
      }
    }

    const gameTooltip = document.getElementById("gameTooltip");

    if (hitNode) {
      hoveredNode = hitNode;
      hoveredCorridor = null;
      playerCanvas.style.cursor = "pointer";
      const isClosed = hitNode.control_state === "BLOCK";
      const nodeType = hitNode.type;

      if (gameTooltip) {
        let actionText = "Click to Toggle";
        if (nodeType === "GATE") {
          actionText = isClosed ? "Click to Open Gate" : "Click to Close Gate";
        } else if (nodeType === "EXIT") {
          actionText = isClosed ? "Click to Open Exit" : "Click to Close Exit";
        } else if (nodeType === "SEATING" || hitNode.id.startsWith("PLAZA")) {
          actionText = isClosed ? "Click to Release Plaza" : "Click to Hold Plaza";
        } else {
          actionText = isClosed ? "Click to Release Junction" : "Click to Hold Junction";
        }
        gameTooltip.textContent = actionText;
        gameTooltip.style.left = `${ev.clientX - rect.left + 14}px`;
        gameTooltip.style.top = `${ev.clientY - rect.top + 14}px`;
        gameTooltip.hidden = false;
      }

      const info = document.getElementById("playerInspectInfo");
      if (info) {
        if (nodeType === "GATE") {
          info.textContent = `${hitNode.name || hitNode.id}: ${isClosed ? "CLOSED (Click to Open)" : "OPEN (Click to Close)"}`;
        } else if (nodeType === "EXIT") {
          info.textContent = `${hitNode.name || hitNode.id}: ${isClosed ? "CLOSED (Click to Open)" : "OPEN (Click to Block)"}`;
        } else if (nodeType === "SEATING" || hitNode.id.startsWith("PLAZA")) {
          info.textContent = `${hitNode.name || hitNode.id}: Event Plaza • ${Math.round(hitNode.current_people || 0)} spectators`;
        } else {
          info.textContent = `${hitNode.name || hitNode.id}: Concourse Junction`;
        }
      }
      return;
    }

    // 2. Corridor Hit Test (perpendicular distance <= max(12 * dpr, 14 * s))
    let hitCorridor = null;
    const nodeMap = {};
    latestPlayerState.graph.nodes.forEach(n => { nodeMap[n.id] = n; });
    const checked = new Set();

    for (const edge of latestPlayerState.graph.edges) {
      const u = edge.source;
      const v = edge.target;
      if ((u === "EMG-01" || v === "EMG-01") && !latestPlayerState.emergency_active) continue;
      const pairKey = u < v ? `${u}--${v}` : `${v}--${u}`;
      if (checked.has(pairKey)) continue;
      checked.add(pairKey);

      const nodeA = nodeMap[u];
      const nodeB = nodeMap[v];
      if (!nodeA || !nodeB) continue;

      const [ax, ay, s] = graphToScreen(nodeA.x, nodeA.y, w, h);
      const [bx, by] = graphToScreen(nodeB.x, nodeB.y, w, h);
      const threshold = Math.max(12 * dpr, 14 * s);
      const dist = pointToSegmentDistance(mx, my, ax, ay, bx, by);
      if (dist <= threshold) {
        hitCorridor = u < v ? { u, v } : { u: v, v: u };
        break;
      }
    }

    if (hitCorridor) {
      hoveredCorridor = hitCorridor;
      hoveredNode = null;
      playerCanvas.style.cursor = "pointer";
      const curState = getCorridorState(latestPlayerState, hitCorridor.u, hitCorridor.v);

      if (gameTooltip) {
        let actionLabel = "Click to cycle direction";
        if (curState === "BIDIRECTIONAL" || !curState) actionLabel = "Click for One-Way ↓";
        else if (curState === "FORWARD_ONLY") actionLabel = "Click for Reverse ↑";
        else if (curState === "REVERSE_ONLY") actionLabel = "Click to Block Corridor ✕";
        else actionLabel = "Click to restore Two-Way ↔";
        gameTooltip.textContent = actionLabel;
        gameTooltip.style.left = `${ev.clientX - rect.left + 14}px`;
        gameTooltip.style.top = `${ev.clientY - rect.top + 14}px`;
        gameTooltip.hidden = false;
      }

      const info = document.getElementById("playerInspectInfo");
      if (info) {
        const stateLabels = {
          "BIDIRECTIONAL": "TWO-WAY ↔ (Click for One-Way ↓)",
          "FORWARD_ONLY": `ONE-WAY ${hitCorridor.u} → ${hitCorridor.v} (Click for Reverse ↑)`,
          "REVERSE_ONLY": `ONE-WAY ${hitCorridor.v} → ${hitCorridor.u} (Click for Barrier ✕)`,
          "BLOCK": "BARRIER ✕ (Click for Two-Way ↔)"
        };
        info.textContent = `Hallway ${hitCorridor.u} ↔ ${hitCorridor.v}: ${stateLabels[curState] || curState}`;
      }
    } else {
      hoveredNode = null;
      hoveredCorridor = null;
      playerCanvas.style.cursor = "default";
      if (gameTooltip) gameTooltip.hidden = true;
      const info = document.getElementById("playerInspectInfo");
      if (info) {
        info.textContent = "Click any gate to OPEN/CLOSE. Click any corridor to cycle 1-WAY / 2-WAY / BARRIER.";
      }
    }
  }

  function onPlayerMouseLeave() {
    hoveredNode = null;
    hoveredCorridor = null;
    if (playerCanvas) playerCanvas.style.cursor = "default";
    const gameTooltip = document.getElementById("gameTooltip");
    if (gameTooltip) gameTooltip.hidden = true;
    const info = document.getElementById("playerInspectInfo");
    if (info) {
      info.textContent = "Click any gate to OPEN/CLOSE. Click any corridor to cycle 1-WAY / 2-WAY / BARRIER.";
    }
  }

  async function onPlayerCanvasClick(ev) {
    if (!latestPlayerState || !latestPlayerState.graph) return;

    // 0. Level 1 Crate Click Handling
    if (hoveredCrate) {
      const crate = hoveredCrate;
      crate.active = !crate.active;
      const nextState = crate.active ? "BLOCK" : "BIDIRECTIONAL";
      playGateToggleSound(crate.active);

      const res = await apiPost("/api/control/corridor", {
        arena: "player",
        source: crate.edge[0],
        target: crate.edge[1],
        state: nextState
      });

      if (res && res.ok === false) {
        crate.active = !crate.active;
        showSafetyToast(res.error || "CANNOT PLACE CRATE HERE");
        playHazardSound();
      } else {
        const info = document.getElementById("playerInspectInfo");
        if (info) {
          info.textContent = `${crate.name}: ${crate.active ? "BLOCKED (✕ ROUTE CLOSED BY CRATE)" : "OPEN (↔ ROUTE RESTORED)"}`;
        }
      }
      return;
    }

    // 0. Level 1 Rotatable Barrier Click Handling
    if (hoveredBarrier) {
      const barrier = hoveredBarrier;
      
      // Cycle through 4 states:
      barrier.stateIndex = (barrier.stateIndex === undefined) ? 1 : barrier.stateIndex + 1;
      if (barrier.stateIndex > 3) barrier.stateIndex = 0;
      
      let straightState = "BIDIRECTIONAL";
      let divertState = "BIDIRECTIONAL";
      let label = "";
      
      if (barrier.stateIndex === 0) {
        straightState = "BIDIRECTIONAL"; divertState = "BIDIRECTIONAL"; barrier.angle = 0; label = "ALL OPEN";
      } else if (barrier.stateIndex === 1) {
        straightState = "BLOCK"; divertState = "BIDIRECTIONAL"; barrier.angle = 90; label = "DIVERT ONLY (↰)";
      } else if (barrier.stateIndex === 2) {
        straightState = "BIDIRECTIONAL"; divertState = "BLOCK"; barrier.angle = 180; label = "STRAIGHT ONLY (↓)";
      } else if (barrier.stateIndex === 3) {
        straightState = "BLOCK"; divertState = "BLOCK"; barrier.angle = 270; label = "BOTH CLOSED (✕)";
      }
      playCorridorCycleSound("BLOCK");
      // Natively update BOTH edges on the backend
      await apiPost("/api/control/corridor", {
        arena: "player", source: barrier.straightEdge[0], target: barrier.straightEdge[1], state: straightState
      });
      await apiPost("/api/control/corridor", {
        arena: "player", source: barrier.divertEdge[0], target: barrier.divertEdge[1], state: divertState
      });
      const info = document.getElementById("playerInspectInfo");
      if (info) {
        info.textContent = `${barrier.name}: ${label}`;
      }
      return;
    }

    // A. Gate / Node Toggle
    if (hoveredNode) {
      const node = hoveredNode;
      const isBlocked = node.control_state === "BLOCK";
      const nextAction = isBlocked ? "NORMAL" : "BLOCK";

      // Immediate Optimistic UI update
      node.control_state = nextAction;
      playGateToggleSound(nextAction === "BLOCK");

      const res = await apiPost("/api/control/action", {
        arena: "player",
        checkpoint_id: node.id,
        action: nextAction,
        duration_sec: 30.0,
        reason: "Tactile map command"
      });

      if (res && res.ok === false) {
        // Rollback optimistic update
        node.control_state = isBlocked ? "BLOCK" : "NORMAL";
        showSafetyToast(res.error || "CANNOT DISCONNECT ALL EXITS");
        playHazardSound();
      } else {
        const info = document.getElementById("playerInspectInfo");
        if (info) {
          info.textContent = `Gate ${node.id} ${nextAction === "BLOCK" ? "CLOSED (BARRIER ENGAGED)" : "OPEN (NORMAL FLOW)"}`;
        }
      }
      return;
    }

    // B. 4-State Corridor Cycle
    if (hoveredCorridor) {
      const { u, v } = hoveredCorridor;
      const curState = getCorridorState(latestPlayerState, u, v);
      let nextState = "FORWARD_ONLY";
      if (curState === "FORWARD_ONLY") nextState = "REVERSE_ONLY";
      else if (curState === "REVERSE_ONLY") nextState = "BLOCK";
      else if (curState === "BLOCK") nextState = "BIDIRECTIONAL";
      else nextState = "FORWARD_ONLY";

      // Immediate Optimistic UI update
      const eFwd = latestPlayerState.graph.edges.find(e => e.source === u && e.target === v);
      const eRev = latestPlayerState.graph.edges.find(e => e.source === v && e.target === u);
      const prevFwd = eFwd ? { enabled: eFwd.enabled, control_state: eFwd.control_state, direction: eFwd.direction } : null;
      const prevRev = eRev ? { enabled: eRev.enabled, control_state: eRev.control_state, direction: eRev.direction } : null;

      if (nextState === "BIDIRECTIONAL") {
        if (eFwd) { eFwd.enabled = true; eFwd.control_state = "NORMAL"; eFwd.direction = "BIDIRECTIONAL"; }
        if (eRev) { eRev.enabled = true; eRev.control_state = "NORMAL"; eRev.direction = "BIDIRECTIONAL"; }
      } else if (nextState === "FORWARD_ONLY") {
        if (eFwd) { eFwd.enabled = true; eFwd.control_state = "NORMAL"; eFwd.direction = "FORWARD_ONLY"; }
        if (eRev) { eRev.enabled = false; eRev.control_state = "BLOCK"; eRev.direction = "FORWARD_ONLY"; }
      } else if (nextState === "REVERSE_ONLY") {
        if (eFwd) { eFwd.enabled = false; eFwd.control_state = "BLOCK"; eFwd.direction = "REVERSE_ONLY"; }
        if (eRev) { eRev.enabled = true; eRev.control_state = "NORMAL"; eRev.direction = "REVERSE_ONLY"; }
      } else if (nextState === "BLOCK") {
        if (eFwd) { eFwd.enabled = false; eFwd.control_state = "BLOCK"; eFwd.direction = "BLOCK"; }
        if (eRev) { eRev.enabled = false; eRev.control_state = "BLOCK"; eRev.direction = "BLOCK"; }
      }
      playCorridorCycleSound(nextState);

      const res = await apiPost("/api/control/corridor", {
        arena: "player",
        source: u,
        target: v,
        state: nextState
      });

      if (res && res.ok === false) {
        // Rollback optimistic update
        if (eFwd && prevFwd) Object.assign(eFwd, prevFwd);
        if (eRev && prevRev) Object.assign(eRev, prevRev);
        showSafetyToast(res.error || "CANNOT DISCONNECT ALL EXITS");
        playHazardSound();
      } else {
        const info = document.getElementById("playerInspectInfo");
        const dirLabel = nextState === "BIDIRECTIONAL" ? "TWO-WAY (↔)" : (nextState === "FORWARD_ONLY" ? `${u} → ${v}` : (nextState === "REVERSE_ONLY" ? `${v} → ${u}` : "BLOCKED BARRIER (✕)"));
        if (info) info.textContent = `Corridor ${u} ↔ ${v} set to ${dirLabel}`;
      }
    }
  }

  // ------------------------------------------------------------- Render Loop
  function renderLoop() {
    const now = performance.now();
    if (playerCtx && playerCanvas) {
      drawArena(playerCtx, playerCanvas, latestPlayerState, true, now);
    }
    if (aiCtx && aiCanvas) {
      drawArena(aiCtx, aiCanvas, latestAiState, false, now);
    }
    requestAnimationFrame(renderLoop);
  }

  function drawTempleArena(ctx, canvas, state, isPlayer, now) {
    const w = canvas.width;
    const h = canvas.height;
    const graph = state.graph;
    const nodeById = {};
    graph.nodes.forEach(n => { nodeById[n.id] = n; });

    // 1. Calculate Viewport & Scale for 1280 x 853 Temple Layout
    const gw = 1280;
    const gh = 853;
    const margin = 8;
    const scale = Math.min((w - margin * 2) / gw, (h - margin * 2) / gh);
    const offsetX = (w - gw * scale) / 2;
    const offsetY = (h - gh * scale) / 2;
    const wPx = gw * scale;
    const hPx = gh * scale;

    // 2. Authentic Temple Map Background (level1.png)
    ctx.save();
    if (level1BgImg.complete && level1BgImg.naturalWidth > 0) {
      ctx.drawImage(level1BgImg, offsetX, offsetY, wPx, hPx);
      // Subtle ambient scrim so particles and controls pop
      ctx.fillStyle = "rgba(10, 14, 22, 0.16)";
      ctx.fillRect(offsetX, offsetY, wPx, hPx);
    } else {
      ctx.fillStyle = "#15120e";
      ctx.fillRect(offsetX, offsetY, wPx, hPx);
    }
    ctx.strokeStyle = "rgba(255, 255, 255, 0.14)";
    ctx.lineWidth = 1.5 * scale;
    ctx.strokeRect(offsetX, offsetY, wPx, hPx);
    ctx.restore();

    // 3. Walkways & Dynamic Corridors
    const renderedPairs = new Set();
    for (const edge of graph.edges) {
      const u = edge.source;
      const v = edge.target;
      if ((u === "EMG-01" || v === "EMG-01") && !state.emergency_active) continue;

      const pairKey = u < v ? `${u}--${v}` : `${v}--${u}`;
      if (renderedPairs.has(pairKey)) continue;
      renderedPairs.add(pairKey);

      const a = nodeById[u < v ? u : v];
      const b = nodeById[u < v ? v : u];
      if (!a || !b) continue;

      const [ax, ay] = graphToScreen(a.x, a.y, w, h);
      const [bx, by] = graphToScreen(b.x, b.y, w, h);
      const corridorState = getCorridorState(state, a.id, b.id);
      const isBlocked = corridorState === "BLOCK" || !edge.enabled;

      ctx.save();
      ctx.beginPath();
      ctx.moveTo(ax, ay);
      ctx.lineTo(bx, by);
      ctx.lineWidth = 15 * scale;
      ctx.lineCap = "round";
      ctx.strokeStyle = isBlocked ? "rgba(239, 68, 68, 0.45)" : "rgba(255, 255, 255, 0.10)";
      ctx.stroke();

      if (!isBlocked) {
        const dx = bx - ax;
        const dy = by - ay;
        const len = Math.hypot(dx, dy);
        const ang = Math.atan2(dy, dx);
        drawFlowingChevrons(ctx, ax, ay, bx, by, len, ang, scale, now, "rgba(251, 191, 36, 0.55)");
      }
      ctx.restore();
    }

    // 4. Moving Crowd Particles
    if (state.particles && state.particles.length) {
      ctx.save();
      const stdRadius = Math.max(3.8, 5.2 * scale);
      for (const p of state.particles) {
        const [px, py] = graphToScreen(p.x, p.y, w, h);
        const stage = (p.stage || "").toUpperCase();

        ctx.beginPath();
        ctx.arc(px, py, stdRadius, 0, Math.PI * 2);

        // 4-stage lifecycle particles: INGRESS, DWELL, EGRESS, EVACUATED
        // Strict Binary 2-Speed & 2-Color System:
        // FAST: Pure White (#ffffff) | SLOW: Bright Danger Red (#ff334b)
        if (p.is_congested || p.is_slow) {
          // Slow: Bright Danger Red
          ctx.fillStyle = "#ff334b";
          ctx.fill();
          ctx.strokeStyle = "#7f1d1d";
          ctx.lineWidth = 1.0 * scale;
          ctx.stroke();
        } else {
          // Fast: Pure White
          ctx.fillStyle = "#ffffff";
          ctx.fill();
          ctx.strokeStyle = "#0b0f17";
          ctx.lineWidth = 1.0 * scale;
          ctx.stroke();
        }
      }
      ctx.restore();
    }

    // 5. Tactical Wooden Crates (box.png)
    const crates = isPlayer ? playerCrates : aiCrates;
    for (const cid in crates) {
      const crate = crates[cid];
      const [cx, cy] = graphToScreen(crate.x, crate.y, w, h);
      const boxSize = 38 * scale;
      const isHovered = isPlayer && hoveredCrate && hoveredCrate.id === cid;

      ctx.save();
      if (crate.active) {
        if (boxSprite.complete && boxSprite.naturalWidth > 0) {
          ctx.drawImage(boxSprite, cx - boxSize / 2, cy - boxSize / 2, boxSize, boxSize);
        } else {
          ctx.fillStyle = "#78350f";
          ctx.strokeStyle = "#b45309";
          ctx.lineWidth = 2 * scale;
          ctx.fillRect(cx - boxSize / 2, cy - boxSize / 2, boxSize, boxSize);
          ctx.strokeRect(cx - boxSize / 2, cy - boxSize / 2, boxSize, boxSize);
        }

        // Red blocked indicator badge
        ctx.beginPath();
        ctx.arc(cx, cy, boxSize * 0.55, 0, Math.PI * 2);
        ctx.fillStyle = "rgba(220, 38, 38, 0.4)";
        ctx.fill();
        ctx.strokeStyle = "#ef4444";
        ctx.lineWidth = 2.0 * scale;
        ctx.stroke();

        ctx.fillStyle = "#ffffff";
        ctx.font = `800 ${Math.max(9, Math.floor(11 * scale))}px 'Inter', sans-serif`;
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText("✕", cx, cy);
      } else {
        // Ghost placeholder
        ctx.save();
        ctx.setLineDash([4 * scale, 4 * scale]);
        ctx.strokeStyle = isHovered ? "#38bdf8" : "rgba(251, 191, 36, 0.45)";
        ctx.lineWidth = isHovered ? 2.2 * scale : 1.4 * scale;
        ctx.strokeRect(cx - boxSize * 0.45, cy - boxSize * 0.45, boxSize * 0.9, boxSize * 0.9);
        ctx.restore();

        if (isHovered) {
          ctx.fillStyle = "rgba(56, 189, 248, 0.25)";
          ctx.fillRect(cx - boxSize * 0.45, cy - boxSize * 0.45, boxSize * 0.9, boxSize * 0.9);
        }
      }
      ctx.restore();
    }

    // 6. Tactical Rotatable Diverter Barriers (barrier.png)
    const barriers = isPlayer ? playerBarriers : aiBarriers;
    for (const bid in barriers) {
      const barrier = barriers[bid];
      const [bx, by] = graphToScreen(barrier.x, barrier.y, w, h);
      const bW = 46 * scale;
      const bH = 18 * scale;
      const isHovered = isPlayer && hoveredBarrier && hoveredBarrier.id === bid;

      ctx.save();
      ctx.translate(bx, by);
      ctx.rotate((barrier.angle * Math.PI) / 180);

      if (isHovered) {
        ctx.beginPath();
        ctx.arc(0, 0, bW * 0.65, 0, Math.PI * 2);
        ctx.strokeStyle = "#38bdf8";
        ctx.lineWidth = 2.0 * scale;
        ctx.stroke();
      }

      if (barrierSprite.complete && barrierSprite.naturalWidth > 0) {
        ctx.drawImage(barrierSprite, -bW / 2, -bH / 2, bW, bH);
      } else {
        ctx.fillStyle = "#eab308";
        ctx.strokeStyle = "#713f12";
        ctx.lineWidth = 1.6 * scale;
        ctx.fillRect(-bW / 2, -bH / 2, bW, bH);
        ctx.strokeRect(-bW / 2, -bH / 2, bW, bH);
      }

      if (barrier.angle === 90) {
        ctx.fillStyle = "#fef08a";
        ctx.font = `800 ${Math.max(8, Math.floor(9 * scale))}px 'Inter', sans-serif`;
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText("DIVERT ↰", 0, -bH * 0.85);
      }
      ctx.restore();
    }

    // 7. Nodes (Gates, Exits, Checkpoints, Junctions, Seating)
    for (const node of graph.nodes) {
      if (node.id === "EMG-01" && !state.emergency_active) continue;
      const [nx, ny] = graphToScreen(node.x, node.y, w, h);
      const isHovered = isPlayer && hoveredNode && hoveredNode.id === node.id;
      const isBlocked = node.control_state === "BLOCK";
      const isGate = node.type === "GATE";
      const isExit = node.type === "EXIT";
      const r = (isGate || isExit ? 6.0 : 4.5) * scale;
      const nodeColor = NODE_COLORS[node.type] || "#60a5fa";

      ctx.save();

      // Hover / Selection Ring
      if (isHovered) {
        ctx.beginPath();
        ctx.arc(nx, ny, r + 3.0 * scale, 0, Math.PI * 2);
        ctx.strokeStyle = "#38bdf8";
        ctx.lineWidth = 1.5 * scale;
        ctx.stroke();
      }

      // Node Circle
      ctx.beginPath();
      ctx.arc(nx, ny, r, 0, Math.PI * 2);
      ctx.fillStyle = isBlocked ? "#271418" : nodeColor;
      ctx.fill();
      ctx.strokeStyle = isBlocked ? "#ef4444" : (isHovered ? "#ffffff" : "rgba(255, 255, 255, 0.85)");
      ctx.lineWidth = 1.5 * scale;
      ctx.stroke();

      // Blocked barrier marker
      if (isBlocked) {
        drawClosedNodeBarrier(ctx, nx, ny, r, scale);
      }

      // Node Label Typography & Offset
      ctx.font = `600 ${Math.max(8, Math.floor(9.5 * scale))}px 'JetBrains Mono', sans-serif`;
      ctx.fillStyle = isBlocked ? "#fca5a5" : "#e2e8f0";
      ctx.textAlign = "center";
      ctx.textBaseline = "bottom";
      ctx.fillText(node.name || node.id, nx, ny - r - (2.0 * scale));

      ctx.restore();
    }

    // 8. Floating Animated Hazard & Decision Popouts
    if (activePopouts && activePopouts.length) {
      for (let i = activePopouts.length - 1; i >= 0; i--) {
        const pop = activePopouts[i];
        if (pop.arena !== (isPlayer ? "player" : "ai")) continue;
        const elapsed = now - pop.startTime;
        if (elapsed >= pop.duration) {
          activePopouts.splice(i, 1);
          continue;
        }

        const progress = elapsed / pop.duration;
        const [baseX, baseY] = graphToScreen(pop.gx, pop.gy, w, h);
        const floatY = baseY - (progress * 42 * scale);

        let alpha = 1.0;
        if (progress < 0.15) alpha = progress / 0.15;
        else if (progress > 0.75) alpha = 1.0 - (progress - 0.75) / 0.25;

        ctx.save();
        ctx.globalAlpha = Math.max(0, Math.min(1, alpha));
        ctx.translate(baseX, floatY);

        ctx.font = `700 ${10 * scale}px 'JetBrains Mono', monospace`;
        const textMetrics = ctx.measureText(pop.text);
        const pillW = textMetrics.width + 24 * scale;
        const pillH = 22 * scale;
        const pillR = 4 * scale;

        ctx.fillStyle = "rgba(35, 18, 22, 0.96)";
        ctx.strokeStyle = "#ef4444";
        ctx.lineWidth = 1.5 * scale;
        roundRect(ctx, -pillW / 2, -pillH / 2, pillW, pillH, pillR);
        ctx.fill();
        ctx.stroke();

        ctx.font = `600 ${9.5 * scale}px 'JetBrains Mono', monospace`;
        ctx.fillStyle = "#fecaca";
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText(pop.text, 0, 0);

        ctx.restore();
      }
    }

    if (!isPlayer && activeAiBadges && activeAiBadges.length) {
      for (let i = activeAiBadges.length - 1; i >= 0; i--) {
        const badge = activeAiBadges[i];
        const elapsed = now - badge.startTime;
        if (elapsed >= badge.duration) {
          activeAiBadges.splice(i, 1);
          continue;
        }

        const progress = elapsed / badge.duration;
        const [baseX, baseY] = graphToScreen(badge.gx, badge.gy, w, h);
        const floatY = baseY - (progress * 38 * scale);

        let alpha = 1.0;
        if (progress < 0.15) alpha = progress / 0.15;
        else if (progress > 0.70) alpha = 1.0 - (progress - 0.70) / 0.30;

        ctx.save();
        ctx.globalAlpha = Math.max(0, Math.min(1, alpha));
        ctx.translate(baseX, floatY);

        ctx.font = `600 ${10 * scale}px 'JetBrains Mono', monospace`;
        const textMetrics = ctx.measureText(badge.text);
        const pillW = Math.max(128 * scale, textMetrics.width + 20 * scale);
        const pillH = 24 * scale;
        const pillR = 4 * scale;

        ctx.fillStyle = "rgba(26, 23, 38, 0.96)";
        ctx.strokeStyle = "#8b5cf6";
        ctx.lineWidth = 1.5 * scale;
        roundRect(ctx, -pillW / 2, -pillH / 2, pillW, pillH, pillR);
        ctx.fill();
        ctx.stroke();

        ctx.fillStyle = "#ede9fe";
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText(badge.text, 0, 0);

        ctx.restore();
      }
    }
  }

  function drawArena(ctx, canvas, state, isPlayer, now) {
    const w = canvas.width;
    const h = canvas.height;
    ctx.clearRect(0, 0, w, h);

    if (!state || !state.graph) {
      ctx.fillStyle = "rgba(100, 116, 139, 0.4)";
      ctx.font = `${Math.max(12, Math.floor(w / 35))}px 'JetBrains Mono', monospace`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText("SYNCHRONIZING ARENA TELEMETRY...", w / 2, h / 2);
      return;
    }

    if (currentLevel === 1 || currentLevel === 2 || (levelBgImg.complete && levelBgImg.naturalWidth > 0 && currentLevel !== 3)) {
      drawTempleArena(ctx, canvas, state, isPlayer, now);
      return;
    }

    const graph = state.graph;
    const nodeById = {};
    graph.nodes.forEach(n => { nodeById[n.id] = n; });

    // 1. Architectural Stadium Perimeter & Top-to-Bottom Zoning
    const [cx, cy, s] = graphToScreen(500, 350, w, h);
    const [xMin, yMin] = graphToScreen(60, 40, w, h);
    const [xMax, yMax] = graphToScreen(940, 660, w, h);
    const pW = xMax - xMin;
    const pH = yMax - yMin;

    ctx.save();
    // Structural architectural building perimeter
    roundRect(ctx, xMin, yMin, pW, pH, 12 * s);
    ctx.fillStyle = "#0a0e16";
    ctx.fill();
    ctx.strokeStyle = "#1b2332";
    ctx.lineWidth = 1.6 * s;
    ctx.stroke();

    // ------------------------------------------------------------- ZONE 2: CENTRAL EVENT PLAZAS (y ≈ 310 to 410)
    let dwellCount = 0;
    if (state.particles && state.particles.length) {
      for (const p of state.particles) {
        const st = (p.stage || "").toUpperCase();
        if (st === "DWELL" || st === "CENTER_DWELL") dwellCount++;
      }
    }

    const [pzX1, pzY1] = graphToScreen(240, 310, w, h);
    const [pzX2, pzY2] = graphToScreen(760, 410, w, h);
    const pzW = pzX2 - pzX1;
    const pzH = pzY2 - pzY1;

    roundRect(ctx, pzX1, pzY1, pzW, pzH, 8 * s);
    ctx.fillStyle = dwellCount > 0 ? "rgba(245, 158, 11, 0.04)" : "rgba(255, 255, 255, 0.015)";
    ctx.fill();
    ctx.strokeStyle = dwellCount > 0 ? "rgba(245, 158, 11, 0.2)" : "rgba(255, 255, 255, 0.05)";
    ctx.lineWidth = 1.0 * s;
    ctx.setLineDash([4 * s, 4 * s]);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.restore();

    // ------------------------------------------------------------- 2. Wide Architectural Corridors (Walkways)
    const renderedPairs = new Set();

    for (const edge of graph.edges) {
      const u = edge.source;
      const v = edge.target;
      // Suppress inactive EMG-01 emergency diagonal chord cutting across the pitch
      if ((u === "EMG-01" || v === "EMG-01") && !state.emergency_active) {
        continue;
      }

      const pairKey = u < v ? `${u}--${v}` : `${v}--${u}`;
      if (renderedPairs.has(pairKey)) continue;
      renderedPairs.add(pairKey);

      const a = nodeById[u < v ? u : v];
      const b = nodeById[u < v ? v : u];
      if (!a || !b) continue;

      const [ax, ay] = graphToScreen(a.x, a.y, w, h);
      const [bx, by] = graphToScreen(b.x, b.y, w, h);
      const corridorState = getCorridorState(state, a.id, b.id);
      const isHovered = isPlayer && hoveredCorridor && (
        (hoveredCorridor.u === a.id && hoveredCorridor.v === b.id) ||
        (hoveredCorridor.u === b.id && hoveredCorridor.v === a.id)
      );

      const dx = bx - ax;
      const dy = by - ay;
      const len = Math.hypot(dx, dy);
      const ang = Math.atan2(dy, dx);
      const mx = (ax + bx) / 2;
      const my = (ay + by) / 2;

      // AI Intervention Highlight on corridor
      const isAiIntervened = !isPlayer && state.active_decision && (
        state.active_decision.intervention_location === a.id ||
        state.active_decision.intervention_location === b.id
      );

      ctx.save();

      // Outer Walkway Border
      ctx.beginPath();
      ctx.moveTo(ax, ay);
      ctx.lineTo(bx, by);
      ctx.lineCap = "round";
      ctx.lineWidth = isHovered ? 17 * s : 15 * s;
      ctx.strokeStyle = isHovered ? "#38bdf8" : (isAiIntervened ? "rgba(167, 139, 250, 0.55)" : "#222938");
      ctx.stroke();

      // Inner Walkway Floor Fill
      ctx.beginPath();
      ctx.moveTo(ax, ay);
      ctx.lineTo(bx, by);
      ctx.lineWidth = 13 * s;
      ctx.strokeStyle = "#161b24";
      ctx.stroke();

      if (corridorState === "BLOCK") {
        // State 4: BLOCKED BARRIER (✕)
        ctx.beginPath();
        ctx.moveTo(ax, ay);
        ctx.lineTo(bx, by);
        ctx.strokeStyle = "rgba(239, 68, 68, 0.6)";
        ctx.lineWidth = 1.8 * s;
        ctx.setLineDash([5 * s, 4 * s]);
        ctx.stroke();
        ctx.setLineDash([]);

        // Barricade marker across midpoint
        drawBarricade(ctx, mx, my, ang, s);
      } else if (corridorState === "FORWARD_ONLY") {
        // State 2: ONE-WAY FORWARD (a -> b)
        drawFlowingChevrons(ctx, ax, ay, bx, by, len, ang, s, now, isPlayer ? "#38bdf8" : "#a78bfa");
      } else if (corridorState === "REVERSE_ONLY") {
        // State 3: ONE-WAY REVERSE (b -> a)
        drawFlowingChevrons(ctx, bx, by, ax, ay, len, ang + Math.PI, s, now, isPlayer ? "#38bdf8" : "#a78bfa");
      } else {
        // State 1: TWO-WAY (↔) — Clean subtle centerline
        ctx.beginPath();
        ctx.moveTo(ax, ay);
        ctx.lineTo(bx, by);
        ctx.strokeStyle = "rgba(255, 255, 255, 0.08)";
        ctx.lineWidth = 1.2 * s;
        ctx.stroke();

        // Subtle directional tick arrows
        const p1x = ax + dx * 0.35, p1y = ay + dy * 0.35;
        const p2x = ax + dx * 0.65, p2y = ay + dy * 0.65;
        drawArrow(ctx, p1x, p1y, ang, 4.2 * s, "rgba(255, 255, 255, 0.22)");
        drawArrow(ctx, p2x, p2y, ang + Math.PI, 4.2 * s, "rgba(255, 255, 255, 0.22)");
      }

      ctx.restore();
    }

    // ------------------------------------------------------------- 3. Solid Crisp Spectator Tokens (5.2px)
    if (state.particles && state.particles.length) {
      ctx.save();
      const stdRadius = Math.max(3.8, 5.2 * s);
      const dwellRadius = Math.max(4.2, 5.5 * s);

      for (const p of state.particles) {
        const [px, py] = graphToScreen(p.x, p.y, w, h);
        const stage = (p.stage || "").toUpperCase();

        ctx.beginPath();
        ctx.arc(px, py, (stage === "DWELL" || stage === "CENTER_DWELL") ? dwellRadius : stdRadius, 0, Math.PI * 2);

        // Strict Binary 2-Speed & 2-Color System:
        // FAST: Pure White (#ffffff) | SLOW: Bright Danger Red (#ff334b)
        if (p.is_congested || p.is_slow) {
          // Slow: Bright Danger Red
          ctx.fillStyle = "#ff334b";
          ctx.fill();
          ctx.strokeStyle = "#7f1d1d";
          ctx.lineWidth = 1.0 * s;
          ctx.stroke();
        } else {
          // Fast: Pure White
          ctx.fillStyle = "#ffffff";
          ctx.fill();
          ctx.strokeStyle = "#0b0f17";
          ctx.lineWidth = 1.0 * s;
          ctx.stroke();
        }
      }
      ctx.restore();
    }

    // ------------------------------------------------------------- 4. Stadium Nodes (Gates, Exits, Checkpoints, Junctions, Seating)
    for (const node of graph.nodes) {
      if (node.id === "EMG-01" && !state.emergency_active) continue;

      const [nx, ny] = graphToScreen(node.x, node.y, w, h);
      const isClosed = node.control_state === "BLOCK";
      const isGate = node.type === "GATE";
      const isExit = node.type === "EXIT";
      const isHovered = isPlayer && hoveredNode && hoveredNode.id === node.id;
      const isAiIntervened = !isPlayer && state.active_decision && (
        state.active_decision.intervention_location === node.id ||
        state.active_decision.risk_location === node.id
      );

      const r = (isGate || isExit ? 6.0 : 4.5) * s;
      const nodeColor = NODE_COLORS[node.type] || "#60a5fa";
      const risk = node.risk || 0;
      const peopleCount = node.current_people || 0;
      const isHazardProne = (peopleCount / Math.max(1, node.capacity || 10)) >= 0.45 || risk >= 0.50;

      ctx.save();

      // Subtle danger glow
      if (isHazardProne) {
        const pulse = (Math.sin(now * 0.007) + 1) * 0.5;
        const auraRadius = r + (2.5 + pulse * 3.5) * s;
        ctx.beginPath();
        ctx.arc(nx, ny, auraRadius, 0, Math.PI * 2);
        ctx.fillStyle = risk >= 0.75 ? `rgba(239, 68, 68, ${0.16 + pulse * 0.16})` : `rgba(245, 158, 11, ${0.16 + pulse * 0.12})`;
        ctx.fill();
      }

      // Hover Ring / Halo
      if (isHovered) {
        ctx.beginPath();
        ctx.arc(nx, ny, r + 3.0 * s, 0, Math.PI * 2);
        ctx.strokeStyle = "#38bdf8";
        ctx.lineWidth = 1.5 * s;
        ctx.stroke();
      }

      // AI Accent
      if (isAiIntervened) {
        ctx.beginPath();
        ctx.arc(nx, ny, r + 3.0 * s, 0, Math.PI * 2);
        ctx.strokeStyle = "#a78bfa";
        ctx.lineWidth = 1.5 * s;
        ctx.stroke();
      }

      // Node Circle
      ctx.beginPath();
      ctx.arc(nx, ny, r, 0, Math.PI * 2);
      ctx.fillStyle = isClosed ? "#271418" : nodeColor;
      ctx.fill();
      ctx.strokeStyle = isClosed ? "#ef4444" : (isHovered ? "#ffffff" : "rgba(255, 255, 255, 0.85)");
      ctx.lineWidth = 1.5 * s;
      ctx.stroke();

      if (isClosed) {
        drawClosedNodeBarrier(ctx, nx, ny, r, s);
      }

      // Node Label Typography & Offset
      ctx.font = `600 ${Math.max(8, Math.floor(9.5 * s))}px 'JetBrains Mono', sans-serif`;
      ctx.fillStyle = isClosed ? "#fca5a5" : "#e2e8f0";
      ctx.textAlign = "center";
      ctx.textBaseline = "bottom";
      ctx.fillText(node.name || node.id, nx, ny - r - (2.0 * s));

      ctx.restore();
    }

    // ------------------------------------------------------------- 5. Floating Animated Hazard Popouts
    for (let i = activePopouts.length - 1; i >= 0; i--) {
      const pop = activePopouts[i];
      if (pop.arena !== (isPlayer ? "player" : "ai")) continue;

      const elapsed = now - pop.startTime;
      if (elapsed >= pop.duration) {
        activePopouts.splice(i, 1);
        continue;
      }

      const progress = elapsed / pop.duration;
      const [baseX, baseY] = graphToScreen(pop.gx, pop.gy, w, h);
      const floatY = baseY - (progress * 44 * s);

      let alpha = 1.0;
      if (progress < 0.15) alpha = progress / 0.15;
      else if (progress > 0.65) alpha = 1.0 - (progress - 0.65) / 0.35;

      ctx.save();
      ctx.globalAlpha = Math.max(0, Math.min(1, alpha));
      ctx.translate(baseX, floatY);

      const pillW = 142 * s;
      const pillH = 24 * s;
      const pillR = 4 * s;

      ctx.fillStyle = "rgba(35, 18, 22, 0.96)";
      ctx.strokeStyle = "#ef4444";
      ctx.lineWidth = 1.5 * s;
      roundRect(ctx, -pillW / 2, -pillH / 2, pillW, pillH, pillR);
      ctx.fill();
      ctx.stroke();

      ctx.font = `600 ${9.5 * s}px 'JetBrains Mono', monospace`;
      ctx.fillStyle = "#fecaca";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(pop.text, 0, 0);

      ctx.restore();
    }

    // ------------------------------------------------------------- 6. Floating Animated AI Action Badges (AI Arena Only)
    if (!isPlayer && activeAiBadges && activeAiBadges.length) {
      for (let i = activeAiBadges.length - 1; i >= 0; i--) {
        const badge = activeAiBadges[i];
        const elapsed = now - badge.startTime;
        if (elapsed >= badge.duration) {
          activeAiBadges.splice(i, 1);
          continue;
        }

        const progress = elapsed / badge.duration;
        const [baseX, baseY] = graphToScreen(badge.gx, badge.gy, w, h);
        const floatY = baseY - (progress * 38 * s);

        let alpha = 1.0;
        if (progress < 0.15) alpha = progress / 0.15;
        else if (progress > 0.70) alpha = 1.0 - (progress - 0.70) / 0.30;

        ctx.save();
        ctx.globalAlpha = Math.max(0, Math.min(1, alpha));
        ctx.translate(baseX, floatY);

        ctx.font = `600 ${10 * s}px 'JetBrains Mono', monospace`;
        const textMetrics = ctx.measureText(badge.text);
        const pillW = Math.max(128 * s, textMetrics.width + 20 * s);
        const pillH = 24 * s;
        const pillR = 4 * s;

        ctx.fillStyle = "rgba(26, 23, 38, 0.96)";
        ctx.strokeStyle = "#8b5cf6";
        ctx.lineWidth = 1.5 * s;
        roundRect(ctx, -pillW / 2, -pillH / 2, pillW, pillH, pillR);
        ctx.fill();
        ctx.stroke();

        ctx.fillStyle = "#ede9fe";
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText(badge.text, 0, 0);

        ctx.restore();
      }
    }
  }

  // ------------------------------------------------------------- Drawing Helpers
  function drawFlowingChevrons(ctx, x1, y1, x2, y2, len, ang, s, now, color = "rgba(255, 255, 255, 0.9)") {
    if (len < 10) return;
    const count = Math.max(2, Math.floor(len / (36 * s)));
    const speed = 0.0007;
    const chevronSize = 5.5 * s;

    for (let i = 0; i < count; i++) {
      const phase = (i / count + now * speed) % 1.0;
      const px = x1 + (x2 - x1) * phase;
      const py = y1 + (y2 - y1) * phase;

      ctx.save();
      ctx.translate(px, py);
      ctx.rotate(ang);
      ctx.beginPath();
      ctx.moveTo(-chevronSize * 0.6, -chevronSize * 0.6);
      ctx.lineTo(chevronSize * 0.6, 0);
      ctx.lineTo(-chevronSize * 0.6, chevronSize * 0.6);
      ctx.strokeStyle = color;
      ctx.lineWidth = 1.6 * s;
      ctx.lineCap = "round";
      ctx.stroke();
      ctx.restore();
    }
  }

  function drawBarricade(ctx, mx, my, ang, s) {
    ctx.save();
    ctx.translate(mx, my);
    ctx.rotate(ang + Math.PI / 2);

    const bW = 20 * s;
    const bH = 6 * s;

    // Barricade backing
    ctx.fillStyle = "#2d1217";
    ctx.strokeStyle = "#ef4444";
    ctx.lineWidth = 1.4 * s;
    ctx.fillRect(-bW / 2, -bH / 2, bW, bH);
    ctx.strokeRect(-bW / 2, -bH / 2, bW, bH);

    // Diagonal hazard stripes
    ctx.strokeStyle = "#fca5a5";
    ctx.lineWidth = 1.4 * s;
    [-6 * s, -1 * s, 4 * s].forEach(ox => {
      ctx.beginPath();
      ctx.moveTo(ox - 2 * s, bH / 2);
      ctx.lineTo(ox + 2 * s, -bH / 2);
      ctx.stroke();
    });

    ctx.restore();
  }

  function drawClosedNodeBarrier(ctx, nx, ny, r, s) {
    ctx.save();
    ctx.strokeStyle = "#ffffff";
    ctx.lineWidth = 1.8 * s;
    ctx.lineCap = "round";

    const offset = r * 0.55;
    ctx.beginPath();
    ctx.moveTo(nx - offset, ny + offset);
    ctx.lineTo(nx + offset, ny - offset);
    ctx.stroke();

    ctx.beginPath();
    ctx.moveTo(nx - offset, ny);
    ctx.lineTo(nx, ny - offset);
    ctx.stroke();

    ctx.beginPath();
    ctx.moveTo(nx, ny + offset);
    ctx.lineTo(nx + offset, ny);
    ctx.stroke();

    ctx.restore();
  }

  function drawArrow(ctx, x, y, angle, size, color) {
    ctx.save();
    ctx.translate(x, y);
    ctx.rotate(angle);
    ctx.beginPath();
    ctx.moveTo(size, 0);
    ctx.lineTo(-size * 0.6, size * 0.55);
    ctx.lineTo(-size * 0.6, -size * 0.55);
    ctx.closePath();
    ctx.fillStyle = color;
    ctx.fill();
    ctx.restore();
  }

  function roundRect(ctx, x, y, width, height, radius) {
    ctx.beginPath();
    ctx.moveTo(x + radius, y);
    ctx.lineTo(x + width - radius, y);
    ctx.quadraticCurveTo(x + width, y, x + width, y + radius);
    ctx.lineTo(x + width, y + height - radius);
    ctx.quadraticCurveTo(x + width, y + height, x + width - radius, y + height);
    ctx.lineTo(x + radius, y + height);
    ctx.quadraticCurveTo(x, y + height, x, y + height - radius);
    ctx.lineTo(x, y + radius);
    ctx.quadraticCurveTo(x, y, x + radius, y);
    ctx.closePath();
  }

  // ------------------------------------------------------------- Level Unlocking & Route Management
  let maxUnlockedLevel = 1;
  try {
    const saved = localStorage.getItem("crowdshield_max_unlocked");
    if (saved) {
      const n = parseInt(saved, 10);
      if (!isNaN(n) && n >= 1 && n <= 3) maxUnlockedLevel = n;
    }
  } catch (e) {}

  function updateMapButtons() {
    // Level 1: always unlocked
    const btn1 = document.getElementById("btnLaunchLvl1");
    const img1 = document.getElementById("imgLvl1");
    if (btn1) {
      btn1.classList.remove("locked");
      btn1.title = "Play Level 1 (Novice)";
    }
    if (img1) img1.src = "l1_ul.png";

    // Level 2: unlocked if maxUnlockedLevel >= 2
    const btn2 = document.getElementById("btnLaunchLvl2");
    const img2 = document.getElementById("imgLvl2");
    if (btn2) {
      const isL2Unlocked = maxUnlockedLevel >= 2;
      btn2.classList.toggle("locked", !isL2Unlocked);
      btn2.title = isL2Unlocked ? "Play Level 2 (Pro)" : "Level 2 (Pro) - Clear Level 1 to Unlock";
      if (img2) img2.src = isL2Unlocked ? "l2_ul.png" : "l2_l.png";
    }

    // Level 3: unlocked if maxUnlockedLevel >= 3
    const btn3 = document.getElementById("btnLaunchLvl3");
    const img3 = document.getElementById("imgLvl3");
    if (btn3) {
      const isL3Unlocked = maxUnlockedLevel >= 3;
      btn3.classList.toggle("locked", !isL3Unlocked);
      btn3.title = isL3Unlocked ? "Play Level 3 (Master)" : "Level 3 (Master) - Clear Level 2 to Unlock";
      if (img3) img3.src = isL3Unlocked ? "l3_ul.png" : "l3_l.png";
    }

    // Also update HUD dropdown buttons so players can't click locked levels from header
    [1, 2, 3].forEach(l => {
      const b = document.getElementById(`btnLvl${l}`);
      if (b) {
        if (l > maxUnlockedLevel) {
          b.disabled = true;
          b.style.opacity = "0.45";
          b.style.cursor = "not-allowed";
        } else {
          b.disabled = false;
          b.style.opacity = "";
          b.style.cursor = "pointer";
        }
      }
    });
  }

  function navigateTo(path, pushState = true) {
    const splash = document.getElementById("splashScreen");
    const landing = document.getElementById("landingScreen");
    const appContainer = document.getElementById("app");
    const modal = document.getElementById("scorecardModal");
    const countdown = document.getElementById("countdownOverlay");

    // Route: Initial Title Splash Screen
    if (path === "/" || path === "" || path === "/title") {
      if (pushState && window.location.pathname !== "/") {
        window.history.pushState(null, "", "/");
      }
      apiPost("/api/simulation/stop").catch(() => {});
      matchEnded = true;
      isCountingDown = false;

      if (modal) modal.hidden = true;
      if (countdown) countdown.hidden = true;
      if (appContainer) appContainer.style.display = "none";
      if (landing) {
        landing.hidden = true;
        landing.style.display = "none";
        const vid = landing.querySelector(".landing-bg-video") || document.getElementById("bgVideo");
        if (vid) vid.pause();
      }
      if (splash) {
        splash.hidden = false;
        splash.classList.remove("fade-out");
        splash.style.display = "flex";
      }
      return;
    }

    // Route: World Map Screen
    if (path === "/map") {
      if (pushState && window.location.pathname !== "/map") {
        window.history.pushState(null, "", "/map");
      }
      // Stop running simulation
      apiPost("/api/simulation/stop").catch(() => {});
      matchEnded = true;
      isCountingDown = false;

      if (modal) modal.hidden = true;
      if (countdown) countdown.hidden = true;
      if (appContainer) appContainer.style.display = "none";
      if (splash) {
        splash.hidden = true;
        splash.style.display = "none";
      }
      if (landing) {
        landing.hidden = false;
        landing.style.display = "flex";
        const vid = landing.querySelector(".landing-bg-video") || document.getElementById("bgVideo");
        if (vid) vid.play().catch(() => {});
      }
      updateMapButtons();
      return;
    }

    // Route: Gameplay Level
    const match = path.match(/^\/level([1-3])$/);
    if (match) {
      const lvl = parseInt(match[1], 10);
      if (lvl > maxUnlockedLevel) {
        navigateTo("/map", pushState);
        return;
      }
      if (pushState && window.location.pathname !== `/level${lvl}`) {
        window.history.pushState(null, "", `/level${lvl}`);
      }
      if (splash) {
        splash.hidden = true;
        splash.style.display = "none";
      }
      if (landing) {
        landing.hidden = true;
        landing.style.display = "none";
        const vid = landing.querySelector(".landing-bg-video") || document.getElementById("bgVideo");
        if (vid) vid.pause();
      }
      if (appContainer) {
        appContainer.style.display = "flex";
      }
      initCanvases();
      loadLevel(lvl);
      return;
    }

    // Default fallback to /
    navigateTo("/", pushState);
  }

  function handleCurrentUrl(pushState = false) {
    const path = window.location.pathname;
    if (path === "/map") {
      navigateTo("/map", pushState);
      return;
    }
    const match = path.match(/^\/level([1-3])$/);
    if (match) {
      const lvl = parseInt(match[1], 10);
      if (lvl <= maxUnlockedLevel) {
        navigateTo(`/level${lvl}`, pushState);
        return;
      } else {
        navigateTo("/map", pushState);
        return;
      }
    }
    // Default to Title Splash Screen for "/" or unhandled routes
    navigateTo("/", pushState && path !== "/");
  }

  // ------------------------------------------------------------- Control Bar & Modal Wiring
  function wireControls() {
    // Initial Title Splash Screen "CLICK TO PLAY" Button
    const btnClickToPlay = document.getElementById("btnClickToPlay");
    if (btnClickToPlay) {
      const startPlayFromSplash = () => {
        playClickSound();
        const splash = document.getElementById("splashScreen");
        if (splash) {
          splash.classList.add("fade-out");
          setTimeout(() => {
            navigateTo("/map");
          }, 280);
        } else {
          navigateTo("/map");
        }
      };
      btnClickToPlay.addEventListener("click", startPlayFromSplash);
      btnClickToPlay.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          startPlayFromSplash();
        }
      });
    }

    // Title / Home Navigation Buttons
    const btnReturnTitle = document.getElementById("btnReturnTitle");
    if (btnReturnTitle) {
      btnReturnTitle.addEventListener("click", () => {
        playClickSound();
        navigateTo("/");
      });
    }

    const btnHudTitle = document.getElementById("btnHudTitle");
    if (btnHudTitle) {
      btnHudTitle.addEventListener("click", () => {
        playClickSound();
        navigateTo("/");
      });
    }

    const btnScorecardHome = document.getElementById("btnScorecardHome");
    if (btnScorecardHome) {
      btnScorecardHome.addEventListener("click", () => {
        playClickSound();
        const modal = document.getElementById("scorecardModal");
        if (modal) modal.hidden = true;
        navigateTo("/");
      });
    }

    // Interactive Landing Screen Level Hitboxes
    [1, 2, 3].forEach(lvl => {
      const btn = document.getElementById(`btnLaunchLvl${lvl}`);
      if (btn) {
        btn.addEventListener("click", () => {
          if (lvl > maxUnlockedLevel) {
            playLockSound();
            btn.classList.remove("shake");
            void btn.offsetWidth; // trigger reflow
            btn.classList.add("shake");
            setTimeout(() => btn.classList.remove("shake"), 400);
            return;
          }
          playClickSound();
          navigateTo(`/level${lvl}`);
        });
      }
    });

    // Level Selection Pills (Header)
    const lvlBtns = [
      { id: "btnLvl1", level: 1 },
      { id: "btnLvl2", level: 2 },
      { id: "btnLvl3", level: 3 },
    ];

    lvlBtns.forEach(({ id, level }) => {
      const btn = document.getElementById(id);
      if (!btn) return;
      btn.addEventListener("click", () => {
        if (level > maxUnlockedLevel) {
          playLockSound();
          return;
        }
        playClickSound();
        navigateTo(`/level${level}`);
      });
    });

    // Return to World Map buttons
    const btnReturnMap = document.getElementById("btnReturnMap");
    if (btnReturnMap) {
      btnReturnMap.addEventListener("click", () => {
        playClickSound();
        navigateTo("/map");
      });
    }

    const btnScorecardMap = document.getElementById("btnScorecardMap");
    if (btnScorecardMap) {
      btnScorecardMap.addEventListener("click", () => {
        playClickSound();
        const modal = document.getElementById("scorecardModal");
        if (modal) modal.hidden = true;
        navigateTo("/map");
      });
    }

    // Match Execution Buttons (Bottom Bar)
    const btnStart = document.getElementById("btnStart");
    if (btnStart) {
      btnStart.addEventListener("click", async () => {
        playClickSound();
        if (isCountingDown) return;
        if (!matchStartTime) matchStartTime = performance.now();
        await apiPost("/api/simulation/start");
        const leadLabel = document.getElementById("raceLeadStatus");
        if (leadLabel) leadLabel.textContent = "DUEL IN PROGRESS";
      });
    }

    let isPaused = false;
    const btnPause = document.getElementById("btnPause");
    if (btnPause) {
      btnPause.addEventListener("click", async () => {
        playClickSound();
        if (isCountingDown) return;
        const iconPause = btnPause.querySelector(".icon-pause");
        const iconPlay = btnPause.querySelector(".icon-play");
        if (isPaused) {
          isPaused = false;
          await apiPost("/api/simulation/start");
          btnPause.classList.remove("paused");
          btnPause.title = "Pause Match";
          if (iconPause) iconPause.style.display = "";
          if (iconPlay) iconPlay.style.display = "none";
          const leadLabel = document.getElementById("raceLeadStatus");
          if (leadLabel) leadLabel.textContent = "DUEL IN PROGRESS";
        } else {
          isPaused = true;
          await apiPost("/api/simulation/stop");
          btnPause.classList.add("paused");
          btnPause.title = "Resume Match";
          if (iconPause) iconPause.style.display = "none";
          if (iconPlay) iconPlay.style.display = "";
          const leadLabel = document.getElementById("raceLeadStatus");
          if (leadLabel) leadLabel.textContent = "MATCH PAUSED";
        }
      });
    }

    const btnReset = document.getElementById("btnReset");
    if (btnReset) {
      btnReset.addEventListener("click", () => {
        playClickSound();
        loadLevel(currentLevel);
      });
    }

    // Level Dropdown Menu
    const btnLevelMenu = document.getElementById("btnLevelMenu");
    const levelMenu = document.getElementById("levelMenu");
    if (btnLevelMenu && levelMenu) {
      btnLevelMenu.addEventListener("click", (e) => {
        e.stopPropagation();
        levelMenu.hidden = !levelMenu.hidden;
      });
      document.addEventListener("click", (e) => {
        if (!levelMenu.hidden && !btnLevelMenu.contains(e.target)) {
          levelMenu.hidden = true;
        }
      });
    }

    // Scorecard Modal Buttons
    const btnRetry = document.getElementById("btnRetryLevel");
    if (btnRetry) {
      btnRetry.addEventListener("click", () => {
        playClickSound();
        const modal = document.getElementById("scorecardModal");
        if (modal) modal.hidden = true;
        loadLevel(currentLevel);
      });
    }

    const btnNext = document.getElementById("btnNextLevel");
    if (btnNext) {
      btnNext.addEventListener("click", () => {
        playClickSound();
        if (currentLevel < 3) {
          const nextLvl = currentLevel + 1;
          if (nextLvl <= maxUnlockedLevel) {
            const modal = document.getElementById("scorecardModal");
            if (modal) modal.hidden = true;
            navigateTo(`/level${nextLvl}`);
          }
        }
      });
    }

    // Sound Toggle
    const btnSound = document.getElementById("btnSoundToggle");
    if (btnSound) {
      btnSound.addEventListener("click", () => {
        soundEnabled = !soundEnabled;
        const iconOn = btnSound.querySelector(".icon-sound-on");
        const iconOff = btnSound.querySelector(".icon-sound-off");
        if (iconOn && iconOff) {
          iconOn.style.display = soundEnabled ? "" : "none";
          iconOff.style.display = soundEnabled ? "none" : "";
        }
        btnSound.title = soundEnabled ? "Mute Sound" : "Enable Sound";
        if (soundEnabled) getAudioCtx();
      });
    }

    // Hidden backward-compat triggers (preserved for unit testing)
    const btnSurge = document.getElementById("btnSurge");
    if (btnSurge) {
      btnSurge.addEventListener("click", () => {
        apiPost("/api/scenario/start", { scenario: "crowd_surge", params: { gate: "GATE-01", count: 70 } });
      });
    }

    const btnCloseExit = document.getElementById("btnCloseExit");
    if (btnCloseExit) {
      let exitClosed = false;
      btnCloseExit.addEventListener("click", () => {
        exitClosed = !exitClosed;
        apiPost("/api/scenario/start", { scenario: exitClosed ? "gate_failure" : "reopen_exit", params: { exit: "EXIT-03" } });
        btnCloseExit.textContent = exitClosed ? "🚪 REOPEN EXIT-03" : "🚪 BLOCK EXIT-03";
      });
    }

    const btnCounterflow = document.getElementById("btnCounterflow");
    if (btnCounterflow) {
      btnCounterflow.addEventListener("click", () => {
        apiPost("/api/scenario/start", { scenario: "counterflow" });
      });
    }

    const btnEmergency = document.getElementById("btnEmergency");
    if (btnEmergency) {
      let emg = false;
      btnEmergency.addEventListener("click", () => {
        emg = !emg;
        apiPost("/api/emergency", { active: emg, responder_start: "GATE-01", responder_destination: "CP-09" });
        btnEmergency.classList.toggle("active", emg);
      });
    }
  }

  // ------------------------------------------------------------- Initial Boot & Fallback
  async function initialSync() {
    try {
      const lvlData = await apiGet("/api/game/level");
      if (lvlData && lvlData.current_level) {
        currentLevel = lvlData.current_level;
      }
      await loadDynamicInteractiveObjects(currentLevel);
      [1, 2, 3].forEach(l => {
        const b = document.getElementById(`btnLvl${l}`);
        if (b) b.classList.toggle("active", l === currentLevel);
      });

      const dualData = await apiGet("/api/crowd-state/dual");
      if (dualData) {
        if (dualData.player) latestPlayerState = dualData.player;
        if (dualData.ai) latestAiState = dualData.ai;
        updateRaceHud();
      }

      updateMapButtons();
      handleCurrentUrl(false);
    } catch (e) {
      updateMapButtons();
      handleCurrentUrl(false);
    }
  }

  // Handle browser back / forward navigation
  window.addEventListener("popstate", () => {
    handleCurrentUrl(false);
  });

  // ------------------------------------------------------------- DOM Ready
  document.addEventListener("DOMContentLoaded", () => {
    initCanvases();
    wireControls();
    connectWS();
    initialSync();
    requestAnimationFrame(renderLoop);
  });

})();
