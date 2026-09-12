/* =========================================================================
   CrowdShield AI — Frontend Application
   No build step: vanilla JS + Canvas2D. Talks to the FastAPI backend over
   REST (one-shot actions) and WebSocket (continuous live state).
   ========================================================================= */
(() => {
  "use strict";

  // ------------------------------------------------------------- config
  const API_BASE = (window.CROWDSHIELD_API_BASE || `${location.protocol}//${location.hostname}:8000`);
  const WS_URL = API_BASE.replace(/^http/, "ws") + "/ws";

  const RISK_COLORS = {
    VERY_LOW: getVar("--risk-verylow"), LOW: getVar("--risk-low"),
    MEDIUM: getVar("--risk-medium"), HIGH: getVar("--risk-high"), CRITICAL: getVar("--risk-critical"),
  };
  function getVar(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#888"; }

  function riskBand(r) {
    if (r < 0.20) return "VERY_LOW";
    if (r < 0.40) return "LOW";
    if (r < 0.60) return "MEDIUM";
    if (r < 0.80) return "HIGH";
    return "CRITICAL";
  }
  function riskColor(r) { return RISK_COLORS[riskBand(r)]; }

  // ------------------------------------------------------------- state
  let latestState = null;
  let ghostParticles = [];
  let selectedHorizon = 0;
  let showGhosts = true;
  let selectedCheckpoint = null;
  let ws = null;
  let wsConnected = false;
  let riskHistory = [];
  let crowdHistory = [];

  // ------------------------------------------------------------- networking
  async function apiPost(path, body) {
    try {
      const res = await fetch(API_BASE + path, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body || {}),
      });
      return await res.json();
    } catch (e) {
      console.error("API error", path, e);
      flashBanner(true);
      return null;
    }
  }
  async function apiGet(path) {
    try {
      const res = await fetch(API_BASE + path);
      return await res.json();
    } catch (e) {
      flashBanner(true);
      return null;
    }
  }

  function flashBanner(show) {
    const el = document.getElementById("connBanner");
    document.getElementById("connUrl").textContent = API_BASE;
    el.hidden = !show;
  }

  function connectWS() {
    try { ws = new WebSocket(WS_URL); } catch (e) { scheduleReconnect(); return; }
    ws.onopen = () => { wsConnected = true; flashBanner(false); };
    ws.onclose = () => { wsConnected = false; scheduleReconnect(); };
    ws.onerror = () => { wsConnected = false; };
    ws.onmessage = (msg) => {
      let data;
      try { data = JSON.parse(msg.data); } catch (e) { return; }
      if (data.type === "crowd_state") {
        latestState = data;
        onStateUpdate(data);
      } else if (data.type === "event") {
        pushEventRow(data);
      }
    };
  }
  function scheduleReconnect() {
    flashBanner(true);
    setTimeout(connectWS, 2500);
  }

  // ------------------------------------------------------------- boot
  document.addEventListener("DOMContentLoaded", () => {
    connectWS();
    initCanvas();
    initCameraCards();
    wireControls();
    requestAnimationFrame(renderLoop);
    // poll ghosts on horizon change / periodically
    setInterval(refreshGhosts, 1200);
    setInterval(refreshCameraFeedAnimation, 1000 / 20);
  });

  // ------------------------------------------------------------- state -> UI
  function onStateUpdate(state) {
    document.getElementById("cctvCount").textContent =
      Object.values(state.cameras).filter(c => c.status !== "OFFLINE").length + "/5";
    document.getElementById("chipAi").querySelector("b").textContent =
      state.shadow_mode ? "SHADOW MODE" : "ACTIVE";
    document.getElementById("emgOverlay").hidden = !state.emergency_active;

    updateMetrics(state.metrics);
    updateCameraCards(state.cameras);
    updateAIInsight(state.active_decision);
    if (selectedCheckpoint) updateCheckpointDetail(selectedCheckpoint);

    riskHistory.push(state.metrics.network_risk);
    crowdHistory.push(state.metrics.total_crowd);
    if (riskHistory.length > 90) riskHistory.shift();
    if (crowdHistory.length > 90) crowdHistory.shift();
    drawSparkline("chartRisk", riskHistory, getVar("--accent-cyan"), 0, 1);
    drawSparkline("chartCrowd", crowdHistory, getVar("--accent-violet"), 0, null);
  }

  function updateMetrics(m) {
    const items = [
      ["TOTAL CROWD", Math.round(m.total_crowd)],
      ["CRITICAL NODES", m.critical_checkpoints],
      ["PREDICTED BOTTLENECKS", m.predicted_bottlenecks],
      ["ACTIVE INTERVENTIONS", m.active_interventions],
      ["NETWORK RISK", m.network_risk.toFixed(2)],
      ["EVAC READINESS", Math.round(m.evacuation_readiness * 100) + "%"],
    ];
    const grid = document.getElementById("metricsGrid");
    grid.innerHTML = items.map(([label, val]) =>
      `<div class="metric-card"><div class="mval">${val}</div><div class="mlabel">${label}</div></div>`
    ).join("");
  }

  function pushEventRow(evt) {
    const log = document.getElementById("eventLog");
    const row = document.createElement("div");
    let cls = "";
    if (evt.type.includes("CRITICAL") || evt.type === "OPERATOR_REVIEW_REQUIRED") cls = "ev-critical";
    else if (evt.type.includes("BOTTLENECK") || evt.type.includes("SURGE") || evt.type.includes("COUNTERFLOW")) cls = "ev-risk";
    else if (evt.type.includes("ACTION") || evt.type.includes("OPTIMAL")) cls = "ev-action";
    else if (evt.type.includes("RESTORED") || evt.type === "ROUTE_RESTORED") cls = "ev-ok";
    else if (evt.type === "SCENARIO_TRIGGER") cls = "ev-scenario";
    row.className = "event-row " + cls;
    row.innerHTML = `<span class="et">${evt.timestamp}</span><span class="em">${escapeHtml(evt.message)}</span>`;
    log.appendChild(row);
    while (log.children.length > 120) log.removeChild(log.firstChild);

    if (evt.type === "OPTIMAL_ACTION_SELECTED" || evt.type === "CHECKPOINT_ACTION") {
      showAIFlash(evt.payload && evt.payload.decision ? evt.payload.decision : evt.payload);
    }
  }

  function escapeHtml(s) { const d = document.createElement("div"); d.textContent = s; return d.innerHTML; }

  // ------------------------------------------------------------- AI insight panel
  function updateAIInsight(decision) {
    const body = document.getElementById("aiInsightBody");
    if (!decision || decision.action === "DO_NOTHING" || !decision.intervention_location) {
      body.innerHTML = `<div class="empty-state">Monitoring stadium state. No elevated risk requiring intervention right now.</div>`;
      return;
    }
    const band = riskBand(decision.risk_without_action);
    body.innerHTML = `
      <div class="risk-badge" style="background:${riskColor(decision.risk_without_action)}22; color:${riskColor(decision.risk_without_action)}; border:1px solid ${riskColor(decision.risk_without_action)}55;">
        ${band.replace("_", " ")}
      </div>
      <div class="insight-row"><span class="k">Risk Location</span><span class="v">${decision.risk_location}</span></div>
      <div class="insight-row"><span class="k">Intervention</span><span class="v">${decision.intervention_location}</span></div>
      <div class="insight-row"><span class="k">Action</span><span class="v">${decision.action.replace(/_/g, " ")}</span></div>
      <div class="insight-row"><span class="k">Duration</span><span class="v">${Math.round(decision.duration_sec)}s</span></div>
      <div class="insight-row"><span class="k">Confidence</span><span class="v">${Math.round(decision.confidence * 100)}%</span></div>
      <div class="insight-compare">
        <div class="cbox"><div class="cval" style="color:${riskColor(decision.risk_without_action)}">${decision.risk_without_action.toFixed(2)}</div><div class="clabel">RISK WITHOUT ACTION</div></div>
        <div class="cbox"><div class="cval" style="color:${riskColor(decision.risk_with_action)}">${decision.risk_with_action.toFixed(2)}</div><div class="clabel">RISK WITH ACTION</div></div>
      </div>
      <div class="insight-why">${escapeHtml(decision.reason)}</div>
    `;
  }

  function showAIFlash(decision) {
    if (!decision || !decision.intervention_location) return;
    const el = document.getElementById("aiFlash");
    el.innerHTML = `
      <h4>AI INTERVENTION</h4>
      <div class="flash-row"><span>Risk Location</span><b>${decision.risk_location || "-"}</b></div>
      <div class="flash-row"><span>Intervention Point</span><b>${decision.intervention_location}</b></div>
      <div class="flash-row"><span>Action</span><b>${(decision.action || "").replace(/_/g, " ")}</b></div>
      <div class="flash-row"><span>Duration</span><b>${Math.round(decision.duration_sec || 0)}s</b></div>
    `;
    el.hidden = false;
    clearTimeout(showAIFlash._t);
    showAIFlash._t = setTimeout(() => { el.hidden = true; }, 6000);
  }

  // ------------------------------------------------------------- checkpoint detail
  async function updateCheckpointDetail(id) {
    const data = await apiGet(`/api/checkpoints/${id}`);
    if (!data || data.error) return;
    const el = document.getElementById("checkpointDetail");
    const band = riskBand(data.risk);
    const pd = data.predicted_density || {};
    el.innerHTML = `
      <div class="cd-title" style="color:${riskColor(data.risk)}">${data.id} — ${band.replace("_"," ")}</div>
      <div class="insight-row"><span class="k">Current Density</span><span class="v">${data.current_density.toFixed(2)} p/m²</span></div>
      <div class="insight-row"><span class="k">Current Flow (in/out)</span><span class="v">${data.inflow.toFixed(1)} / ${data.outflow.toFixed(1)} p/s</span></div>
      <div class="insight-row"><span class="k">Capacity</span><span class="v">${data.capacity} p/s</span></div>
      <div class="insight-row"><span class="k">Time to Critical</span><span class="v">${data.time_to_critical_sec == null ? "—" : Math.round(data.time_to_critical_sec) + "s"}</span></div>
      <div class="insight-row"><span class="k">Control State</span><span class="v">${data.control_state}</span></div>
      <div class="insight-row"><span class="k">+15s / +30s / +60s</span><span class="v">${(pd[15]||0).toFixed(1)} / ${(pd[30]||0).toFixed(1)} / ${(pd[60]||0).toFixed(1)}</span></div>
      <div class="cd-contrib">
        ${Object.entries(data.risk_breakdown || {}).map(([k, v]) => `
          <div class="cd-contrib-row">
            <span class="label">${k.replace(/_/g, " ")}</span>
            <div class="cd-bar"><div class="cd-bar-fill" style="width:${Math.round(v*100)}%; background:${riskColor(v)}"></div></div>
            <span class="pct">${Math.round(v*100)}%</span>
          </div>`).join("")}
      </div>
    `;
  }

  // ------------------------------------------------------------- camera cards
  const cameraCanvases = {};
  function initCameraCards() {
    const list = document.getElementById("cameraList");
    const ids = ["CAM-01", "CAM-02", "CAM-03", "CAM-04", "CAM-05"];
    list.innerHTML = ids.map(id => `
      <div class="camera-card" id="card-${id}">
        <div class="camera-feed">
          <canvas id="feed-${id}" width="240" height="140"></canvas>
          <span class="feed-tag">${id}</span>
          <span class="feed-status status-fallback" id="status-${id}">…</span>
        </div>
        <div class="camera-body">
          <div class="camera-row1"><span class="camera-id">${id}</span><span class="camera-cp" id="cp-${id}">→ —</span></div>
          <div class="camera-stats">
            <span>People <b id="people-${id}">0</b></span>
            <span>Density <b id="density-${id}">0.00</b></span>
            <span>Flow <b id="flow-${id}">0.0</b></span>
          </div>
        </div>
      </div>
    `).join("");
    ids.forEach(id => { cameraCanvases[id] = document.getElementById(`feed-${id}`).getContext("2d"); });
  }

  function updateCameraCards(cameras) {
    for (const [id, cam] of Object.entries(cameras)) {
      const statusEl = document.getElementById(`status-${id}`);
      if (!statusEl) continue;
      statusEl.textContent = cam.status.replace(/_/g, " ");
      statusEl.className = "feed-status " + (
        cam.status === "OFFLINE" ? "status-offline" :
        cam.status === "CV_MOTION_PROXY" ? "status-online" : "status-fallback");
      document.getElementById(`cp-${id}`).textContent = "→ " + (cam.checkpoint_id || "—");
      document.getElementById(`people-${id}`).textContent = Math.round(cam.people_count);
      document.getElementById(`density-${id}`).textContent = cam.density.toFixed(2);
      document.getElementById(`flow-${id}`).textContent = cam.flow_estimate.toFixed(1);
      cameraCanvases[id]._latest = cam;
    }
  }

  const cameraVideos = {};
  function getVideoElement(id) {
    if (!cameraVideos[id]) {
      const vid = document.createElement("video");
      vid.src = `http://127.0.0.1:8000/cameras/camera_${id.slice(-2)}.mp4`;
      vid.autoplay = true;
      vid.loop = true;
      vid.muted = true;
      vid.playsInline = true;
      vid.play().catch(() => {});
      cameraVideos[id] = vid;
    }
    return cameraVideos[id];
  }

  // simulated "video texture": animated scanline/noise field standing in for the
  // (absent) pre-recorded MP4, clearly labelled DEMO/SIMULATION via the status pill.
  function refreshCameraFeedAnimation() {
    const t = performance.now() / 1000;
    for (const [id, ctx] of Object.entries(cameraCanvases)) {
      const cam = ctx._latest;
      const w = ctx.canvas.width, h = ctx.canvas.height;
      
      if (cam && cam.status === "CV_MOTION_PROXY") {
        const vid = getVideoElement(id);
        if (vid.readyState >= 2) {
          ctx.drawImage(vid, 0, 0, w, h);
          // Add a very subtle scanline over the video to keep the aesthetic
          ctx.fillStyle = "rgba(51,216,255,0.08)";
          ctx.fillRect(0, (t * 40) % h, w, 2);
          continue;
        }
      }

      ctx.fillStyle = "#050a12";
      ctx.fillRect(0, 0, w, h);
      const activity = cam ? Math.min(1, cam.density) : 0.1;
      const n = Math.round(6 + activity * 26);
      ctx.save();
      for (let i = 0; i < n; i++) {
        const seed = i * 91.7 + parseInt(id.slice(-2)) * 13.1;
        const bx = (w * ((Math.sin(seed) + 1) / 2) + Math.sin(t * 0.6 + seed) * 14 + w) % w;
        const by = (h * ((Math.cos(seed * 1.3) + 1) / 2) + Math.cos(t * 0.5 + seed) * 10 + h) % h;
        ctx.fillStyle = `rgba(51,216,255,${0.25 + 0.35 * Math.abs(Math.sin(t + seed))})`;
        ctx.beginPath(); ctx.ellipse(bx, by, 3, 5, 0, 0, Math.PI * 2); ctx.fill();
      }
      ctx.strokeStyle = "rgba(51,216,255,0.06)";
      const scan = (t * 40) % h;
      ctx.fillStyle = "rgba(51,216,255,0.05)";
      ctx.fillRect(0, scan, w, 2);
      ctx.restore();
    }
  }

  // ------------------------------------------------------------- stadium twin canvas
  let twinCtx, twinCanvas;
  function initCanvas() {
    twinCanvas = document.getElementById("twinCanvas");
    twinCtx = twinCanvas.getContext("2d");
    const resize = () => {
      const rect = twinCanvas.parentElement.getBoundingClientRect();
      twinCanvas.width = rect.width * devicePixelRatio;
      twinCanvas.height = rect.height * devicePixelRatio;
      twinCanvas.style.width = rect.width + "px";
      twinCanvas.style.height = rect.height + "px";
    };
    resize();
    window.addEventListener("resize", resize);
    twinCanvas.addEventListener("click", onTwinClick);
  }

  function graphToScreen(x, y) {
    const sx = twinCanvas.width / 1000, sy = twinCanvas.height / 700;
    const s = Math.min(sx, sy);
    const offX = (twinCanvas.width - 1000 * s) / 2;
    const offY = (twinCanvas.height - 700 * s) / 2;
    return [x * s + offX, y * s + offY, s];
  }

  function onTwinClick(ev) {
    if (!latestState) return;
    const rect = twinCanvas.getBoundingClientRect();
    const mx = (ev.clientX - rect.left) * devicePixelRatio;
    const my = (ev.clientY - rect.top) * devicePixelRatio;
    let best = null, bestD = 26 * devicePixelRatio;
    for (const n of latestState.graph.nodes) {
      const [sx, sy] = graphToScreen(n.x, n.y);
      const d = Math.hypot(sx - mx, sy - my);
      if (d < bestD) { bestD = d; best = n; }
    }
    if (best) {
      selectedCheckpoint = best.id;
      updateCheckpointDetail(best.id);
    }
  }

  async function refreshGhosts() {
    if (selectedHorizon === 0 || !showGhosts) { ghostParticles = []; return; }
    const data = await apiGet(`/api/predictions/ghosts?horizon=${selectedHorizon}`);
    if (Array.isArray(data)) ghostParticles = data;
  }

  function renderLoop() {
    drawTwin();
    requestAnimationFrame(renderLoop);
  }

  function drawTwin() {
    const ctx = twinCtx;
    const w = twinCanvas.width, h = twinCanvas.height;
    ctx.clearRect(0, 0, w, h);
    if (!latestState) {
      ctx.fillStyle = "#405066"; ctx.font = "14px monospace"; ctx.textAlign = "center";
      ctx.fillText("Connecting to CrowdShield backend…", w / 2, h / 2);
      return;
    }
    const graph = latestState.graph;
    const nodeById = {}; graph.nodes.forEach(n => nodeById[n.id] = n);

    // stadium outline (rough oval)
    const [cx, cy, s] = graphToScreen(500, 350);
    ctx.save();
    ctx.strokeStyle = "rgba(51,216,255,0.10)";
    ctx.lineWidth = 2 * s;
    ctx.beginPath();
    ctx.ellipse(cx, cy, 330 * s, 260 * s, 0, 0, Math.PI * 2);
    ctx.stroke();
    ctx.restore();

    // edges
    for (const e of graph.edges) {
      const a = nodeById[e.source], b = nodeById[e.target];
      if (!a || !b) continue;
      const [ax, ay] = graphToScreen(a.x, a.y);
      const [bx, by] = graphToScreen(b.x, b.y);
      ctx.beginPath();
      ctx.moveTo(ax, ay); ctx.lineTo(bx, by);
      if (e.control_state === "BLOCK" || !e.enabled) {
        ctx.strokeStyle = "rgba(240,72,99,0.55)"; ctx.setLineDash([6 * s, 5 * s]);
      } else {
        const risk = e.risk || 0;
        ctx.strokeStyle = risk > 0.4 ? riskColor(risk) + "88" : "rgba(90,130,180,0.28)";
        ctx.setLineDash([]);
      }
      ctx.lineWidth = Math.max(1, (1 + e.utilization * 2.5)) * s;
      ctx.stroke();
      ctx.setLineDash([]);

      // direction arrow at midpoint
      if (e.current_flow > 0.4 && e.enabled) {
        const mx = (ax + bx) / 2, my = (ay + by) / 2;
        const ang = Math.atan2(by - ay, bx - ax);
        drawArrow(ctx, mx, my, ang, 6 * s, "rgba(51,216,255,0.55)");
      }
      // blocked barrier icon
      if (e.control_state === "BLOCK" || !e.enabled) {
        const mx = (ax + bx) / 2, my = (ay + by) / 2;
        ctx.save(); ctx.translate(mx, my); ctx.rotate(Math.atan2(by - ay, bx - ax) + Math.PI / 2);
        ctx.fillStyle = "#f04863"; ctx.fillRect(-8 * s, -1.5 * s, 16 * s, 3 * s);
        ctx.restore();
      }
    }

    // ghost particles (predicted)
    if (showGhosts && ghostParticles.length) {
      ctx.save();
      for (const g of ghostParticles) {
        const [gx, gy] = graphToScreen(g.x, g.y);
        ctx.beginPath();
        ctx.fillStyle = "rgba(51,216,255,0.28)";
        ctx.arc(gx, gy, 2.4 * s, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.restore();
    }

    // live particles
    ctx.save();
    for (const p of latestState.particles) {
      const [px, py] = graphToScreen(p.x, p.y);
      ctx.beginPath();
      const hue = [51, 91, 122, 200, 260, 30][p.group_id % 6];
      ctx.fillStyle = `hsla(${hue}, 85%, 65%, 0.85)`;
      ctx.arc(px, py, 2.1 * s, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.restore();

    // nodes
    const t = performance.now() / 500;
    for (const n of graph.nodes) {
      const [nx, ny] = graphToScreen(n.x, n.y);
      const color = riskColor(n.risk);
      const baseR = (n.type === "SEATING" ? 15 : n.type === "GATE" || n.type === "EXIT" ? 10 : 8) * s;
      const critical = n.risk >= 0.8;

      if (critical) {
        const pulse = (Math.sin(t) + 1) / 2;
        ctx.beginPath();
        ctx.fillStyle = color + Math.round(20 + pulse * 30).toString(16);
        ctx.arc(nx, ny, baseR + (6 + pulse * 6) * s, 0, Math.PI * 2);
        ctx.fill();
      }

      ctx.beginPath();
      ctx.fillStyle = n.type === "SEATING" ? "#101d30" : "#0e192b";
      ctx.strokeStyle = color;
      ctx.lineWidth = 2 * s;
      if (n.type === "SEATING") { ctx.rect(nx - baseR, ny - baseR * 0.7, baseR * 2, baseR * 1.4); }
      else { ctx.arc(nx, ny, baseR, 0, Math.PI * 2); }
      ctx.fill(); ctx.stroke();

      if (n.camera_id) {
        ctx.beginPath();
        ctx.fillStyle = n.camera_online === false ? "#f04863" : "var(--accent-cyan)".includes("var") ? "#33d8ff" : "#33d8ff";
        ctx.arc(nx + baseR * 0.7, ny - baseR * 0.7, 3 * s, 0, Math.PI * 2);
        ctx.fill();
      }

      if (n.control_state && n.control_state !== "NORMAL") {
        ctx.beginPath();
        ctx.strokeStyle = "#33d8ff";
        ctx.lineWidth = 1.6 * s;
        ctx.setLineDash([3 * s, 3 * s]);
        ctx.arc(nx, ny, baseR + 5 * s, 0, Math.PI * 2);
        ctx.stroke();
        ctx.setLineDash([]);
      }

      if (s > 0.55) {
        ctx.fillStyle = "#7f93b3";
        ctx.font = `${9 * s}px 'JetBrains Mono', monospace`;
        ctx.textAlign = "center";
        ctx.fillText(n.id, nx, ny + baseR + 11 * s);
      }
    }
  }

  function drawArrow(ctx, x, y, angle, size, color) {
    ctx.save();
    ctx.translate(x, y); ctx.rotate(angle);
    ctx.beginPath();
    ctx.moveTo(size, 0); ctx.lineTo(-size * 0.6, size * 0.6); ctx.lineTo(-size * 0.6, -size * 0.6);
    ctx.closePath();
    ctx.fillStyle = color; ctx.fill();
    ctx.restore();
  }

  // ------------------------------------------------------------- sparkline charts
  function drawSparkline(canvasId, series, color, min, max) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    const dpr = devicePixelRatio;
    const rect = canvas.getBoundingClientRect();
    if (canvas.width !== rect.width * dpr) { canvas.width = rect.width * dpr; canvas.height = rect.height * dpr; }
    const ctx = canvas.getContext("2d");
    const w = canvas.width, h = canvas.height;
    ctx.clearRect(0, 0, w, h);
    if (series.length < 2) return;
    const lo = min != null ? min : Math.min(...series);
    const hi = max != null ? max : Math.max(...series, lo + 1);
    ctx.beginPath();
    series.forEach((v, i) => {
      const x = (i / (series.length - 1)) * w;
      const y = h - ((v - lo) / (hi - lo || 1)) * (h - 8) - 4;
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    });
    ctx.strokeStyle = color; ctx.lineWidth = 1.6 * dpr; ctx.stroke();
    ctx.lineTo(w, h); ctx.lineTo(0, h); ctx.closePath();
    ctx.fillStyle = color.replace(")", ",0.12)").replace("rgb", "rgba");
    ctx.globalAlpha = 0.15; ctx.fillStyle = color; ctx.fill(); ctx.globalAlpha = 1;
  }

  // ------------------------------------------------------------- controls
  function wireControls() {
    document.getElementById("horizonToggle").addEventListener("click", (ev) => {
      const btn = ev.target.closest("button"); if (!btn) return;
      [...ev.currentTarget.children].forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      selectedHorizon = parseInt(btn.dataset.h, 10);
      refreshGhosts();
    });
    document.getElementById("toggleGhosts").addEventListener("change", (ev) => {
      showGhosts = ev.target.checked;
    });

    document.getElementById("btnPresentation").addEventListener("click", () => {
      document.body.classList.toggle("presentation");
    });
    document.getElementById("btnShadow").addEventListener("click", async (ev) => {
      const enabling = !ev.currentTarget.classList.contains("active");
      await apiPost("/api/shadow-mode", { enabled: enabling });
      ev.currentTarget.classList.toggle("active", enabling);
    });

    document.getElementById("btnStart").addEventListener("click", () => apiPost("/api/simulation/start"));
    document.getElementById("btnPause").addEventListener("click", () => apiPost("/api/simulation/stop"));
    document.getElementById("btnReset").addEventListener("click", () => apiPost("/api/simulation/reset"));

    document.getElementById("btnSurge").addEventListener("click", () =>
      apiPost("/api/scenario/start", { scenario: "crowd_surge", params: { gate: "GATE-01", count: 70 } }));
    document.getElementById("btnCloseExit").addEventListener("click", (ev) => {
      const closing = ev.currentTarget.textContent.includes("CLOSE");
      apiPost("/api/scenario/start", { scenario: closing ? "gate_failure" : "reopen_exit", params: { exit: "EXIT-03" } });
      ev.currentTarget.textContent = closing ? "🚪 REOPEN EXIT-03" : "🚪 CLOSE EXIT-03";
    });
    document.getElementById("btnCounterflow").addEventListener("click", () =>
      apiPost("/api/scenario/start", { scenario: "counterflow" }));

    document.getElementById("btnPredict").addEventListener("click", () => {
      const btns = document.querySelectorAll("#horizonToggle button");
      const cur = [...btns].findIndex(b => b.classList.contains("active"));
      const next = btns[(cur + 1) % btns.length];
      next.click();
    });
    document.getElementById("btnPlanner").addEventListener("click", async () => {
      const risks = await apiGet("/api/risks");
      if (!risks) return;
      const worst = Object.entries(risks).sort((a, b) => b[1].risk - a[1].risk)[0];
      alert(`Highest-risk node right now: ${worst[0]} (risk ${worst[1].risk.toFixed(2)}).\nThe AI control loop continuously re-plans every ~2s — watch the AI INSIGHT panel and event log for its next decision.`);
    });
    document.getElementById("btnCompare").addEventListener("click", openCompareModal);
    document.getElementById("btnEmergency").addEventListener("click", async (ev) => {
      const activating = !ev.currentTarget.classList.contains("active");
      await apiPost("/api/emergency", { active: activating, responder_start: "GATE-01", responder_destination: "CP-09" });
      ev.currentTarget.classList.toggle("active", activating);
    });

    document.getElementById("closeCompare").addEventListener("click", () => {
      isCompareModalOpen = false;
      if (compareAnimFrame) cancelAnimationFrame(compareAnimFrame);
      document.getElementById("compareModal").hidden = true;
    });
  }

  // ------------------------------------------------------------- compare modal
  class MiniSim {
    constructor(state, intervention, applyIntervention) {
      if (!state) return;
      this.graph = JSON.parse(JSON.stringify(state.graph));
      this.particles = (state.particles || []).map(p => ({...p, route: p.route ? [...p.route] : null}));
      this.intervention = intervention;
      this.applyIntervention = applyIntervention;
      
      this.nodes = {};
      this.graph.nodes.forEach(n => this.nodes[n.id] = n);
      this.edges = this.graph.edges;
      
      // Apply intervention
      if (this.applyIntervention && this.intervention) {
         const loc = this.intervention.intervention_location;
         const action = this.intervention.action;
         if (loc && this.nodes[loc]) {
             if (action.includes("BLOCK") || action.includes("HOLD") || action.includes("REDIRECT") || action.includes("CHANGE")) {
                 this.nodes[loc].control_state = "BLOCK";
                 this.edges.forEach(e => {
                     if (e.source === loc || e.target === loc) e.enabled = false;
                 });
             }
         }
      }
      
      // Assign routes
      this.particles.forEach(p => {
          let best = null, bestD = Infinity;
          for (const n of this.graph.nodes) {
              const d = Math.hypot(n.x - p.x, n.y - p.y);
              if (d < bestD) { bestD = d; best = n; }
          }
          p.currentNode = best ? best.id : (p.route && p.route.length ? p.route[0] : null);
          p.destination = p.destination || p.currentNode;
          if (this.applyIntervention || !p.route || p.route.length === 0) {
              p.route = this.findRoute(p.currentNode, p.destination) || p.route || [];
          }
          p.x = best ? best.x : p.x;
          p.y = best ? best.y : p.y;
      });
      
      this.metrics = { peakRisk: 0, peakDensity: 0, criticalNodes: 0, congestionDuration: 0, avgTravelTime: 0 };
      this.tickCount = 0;
      this.finishedParticles = 0;
    }
    
    findRoute(start, end) {
        let q = [[start]];
        let visited = new Set([start]);
        let iterations = 0;
        while(q.length > 0 && iterations < 2000) {
            iterations++;
            let path = q.shift();
            let curr = path[path.length - 1];
            if (curr === end) return path;
            
            let neighbors = this.edges
              .filter(e => e.enabled !== false && this.nodes[e.target] && this.nodes[e.target].control_state !== "BLOCK")
              .filter(e => e.source === curr)
              .map(e => e.target);
              
            for (let n of neighbors) {
                if (!visited.has(n)) {
                    visited.add(n);
                    q.push([...path, n]);
                }
            }
        }
        return null; // fallback if no route
    }
    
    tick() {
        if (!this.graph) return;
        this.tickCount++;
        
        this.graph.nodes.forEach(n => { n.people = 0; });
        
        this.particles.forEach(p => {
            if (!p.route || p.route.length < 2) {
                if (p.route && p.route.length === 1) this.finishedParticles++;
                p.route = null;
                return;
            }
            const target = this.nodes[p.route[1]];
            if (!target) return;
            
            const dx = target.x - p.x, dy = target.y - p.y;
            const dist = Math.hypot(dx, dy);
            
            let near = 0;
            for (const other of this.particles) {
               if (Math.hypot(other.x - target.x, other.y - target.y) < 40) near++;
            }
            
            let speed = 2.0;
            if (near > 10) speed = 0.8;
            if (near > 20) speed = 0.2;
            
            if (dist < speed) {
                p.currentNode = target.id;
                p.route.shift();
                p.x = target.x; p.y = target.y;
            } else {
                p.x += (dx / dist) * speed; p.y += (dy / dist) * speed;
            }
            
            let best = null, bestD = Infinity;
            for (const n of this.graph.nodes) {
                const d = Math.hypot(n.x - p.x, n.y - p.y);
                if (d < bestD) { bestD = d; best = n; }
            }
            if (best) best.people++;
        });
        
        let currentPeakRisk = 0, currentPeakDensity = 0, critNodes = 0;
        
        this.graph.nodes.forEach(n => {
            let density = n.people / 10.0;
            n.current_density = density;
            n.risk = Math.min(1.0, density / 2.5);
            if (n.risk > currentPeakRisk) currentPeakRisk = n.risk;
            if (density > currentPeakDensity) currentPeakDensity = density;
            if (n.risk >= 0.8) critNodes++;
        });
        
        if (currentPeakRisk > this.metrics.peakRisk) this.metrics.peakRisk = currentPeakRisk;
        if (currentPeakDensity > this.metrics.peakDensity) this.metrics.peakDensity = currentPeakDensity;
        if (critNodes > this.metrics.criticalNodes) this.metrics.criticalNodes = critNodes;
        if (critNodes > 0) this.metrics.congestionDuration++;
        this.metrics.avgTravelTime = this.tickCount / Math.max(1, this.finishedParticles);
    }
  }

  let compareSimLeft = null;
  let compareSimRight = null;
  let compareAnimFrame = null;
  let isCompareModalOpen = false;

  async function openCompareModal() {
    const modal = document.getElementById("compareModal");
    modal.hidden = false;
    isCompareModalOpen = true;

    const statsLeft = document.getElementById("compareStatsLeft");
    const statsRight = document.getElementById("compareStatsRight");

    try {
      let simState = latestState;
      if (!simState) {
          statsLeft.innerHTML = `<div style="color:var(--risk-critical)">Waiting for simulation state...</div>`;
          return;
      }
      
      compareSimLeft = new MiniSim(simState, null, false);
      compareSimRight = new MiniSim(simState, null, true);
    } catch(err) {
      statsLeft.innerHTML = `<div style="color:red">ERROR: ${err.message}<br>${err.stack}</div>`;
      return;
    }
    
    function updateStats(latest, apiFailed) {
      if (!compareSimLeft || !compareSimRight) return;
      statsLeft.innerHTML = statBlock({
        "Peak Network Risk": compareSimLeft.metrics.peakRisk.toFixed(2),
        "Peak Density": compareSimLeft.metrics.peakDensity.toFixed(2) + " p/m²",
        "Critical Nodes": compareSimLeft.metrics.criticalNodes,
        "Congestion Duration": compareSimLeft.metrics.congestionDuration + "s",
        "Average Travel Time": Math.round(compareSimLeft.metrics.avgTravelTime) + "s",
      }) + (apiFailed ? `<div style="color:var(--risk-critical)"><b>Simulation fallback (API error)</b></div>` : `<div>Intervention Applied<b>None</b></div>`);

      statsRight.innerHTML = statBlock({
        "Peak Network Risk": compareSimRight.metrics.peakRisk.toFixed(2),
        "Peak Density": compareSimRight.metrics.peakDensity.toFixed(2) + " p/m²",
        "Critical Nodes": compareSimRight.metrics.criticalNodes,
        "Congestion Duration": compareSimRight.metrics.congestionDuration + "s",
        "Average Travel Time": Math.round(compareSimRight.metrics.avgTravelTime) + "s",
      }) + (apiFailed ? `<div style="color:var(--risk-critical)"><b>Simulation fallback (API error)</b></div>` : `<div>Intervention Applied<b>${latest ? latest.intervention_location + " " + latest.action.replace(/_/g, " ") : "None"}</b></div>`);
    }
    
    updateStats(null, false);
    
    if (compareAnimFrame) cancelAnimationFrame(compareAnimFrame);
    
    function render() {
        if (!isCompareModalOpen) return;
        try {
            if (compareSimLeft) compareSimLeft.tick();
            if (compareSimRight) compareSimRight.tick();
            
            drawSimCanvas("compareLeft", compareSimLeft, false);
            drawSimCanvas("compareRight", compareSimRight, true);
            
            updateStats(compareSimRight.intervention, compareSimRight.intervention && compareSimRight.intervention.error);
        } catch (err) {
            statsLeft.innerHTML = `<div style="color:red">RENDER ERROR: ${err.message}<br>${err.stack}</div>`;
            return;
        }
        compareAnimFrame = requestAnimationFrame(render);
    }
    render();

    // Fetch async
    apiGet("/api/interventions").then(interventions => {
        const latest = interventions && interventions.length ? interventions[interventions.length - 1] : null;
        if (latest && isCompareModalOpen) {
            compareSimLeft = new MiniSim(simState, latest, false);
            compareSimRight = new MiniSim(simState, latest, true);
        } else if (!latest && isCompareModalOpen) {
            compareSimRight.intervention = { error: true };
        }
    }).catch(e => {
        if (isCompareModalOpen && compareSimRight) compareSimRight.intervention = { error: true };
    });
  }

  function statBlock(obj) {
    return Object.entries(obj).map(([k, v]) => `<div>${k}<b>${v}</b></div>`).join("");
  }

  function drawSimCanvas(id, sim, isRight) {
    const canvas = document.getElementById(id);
    if (!canvas || !sim || !sim.graph) return;
    const dpr = devicePixelRatio;
    const rect = canvas.getBoundingClientRect();
    canvas.width = Math.max(300, rect.width) * dpr; 
    canvas.height = Math.max(200, rect.height || 220) * dpr;
    const ctx = canvas.getContext("2d");
    const w = canvas.width, h = canvas.height;
    ctx.clearRect(0, 0, w, h);
    
    const sx = w / 1000, sy = h / 700;
    const s = Math.min(sx, sy);
    const offX = (w - 1000 * s) / 2;
    const offY = (h - 700 * s) / 2;
    function g2s(gx, gy) { return [gx * s + offX, gy * s + offY, s]; }

    // edges
    sim.edges.forEach(e => {
       const a = sim.nodes[e.source], b = sim.nodes[e.target];
       if (!a || !b) return;
       const [ax, ay] = g2s(a.x, a.y);
       const [bx, by] = g2s(b.x, b.y);
       ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(bx, by);
       if (!e.enabled || (a.control_state === "BLOCK" || b.control_state === "BLOCK")) {
           ctx.strokeStyle = "rgba(240,72,99,0.55)"; ctx.setLineDash([4*s, 4*s]);
       } else {
           ctx.strokeStyle = "rgba(90,130,180,0.15)"; ctx.setLineDash([]);
       }
       ctx.lineWidth = 1 * s; ctx.stroke(); ctx.setLineDash([]);
    });

    // nodes
    sim.graph.nodes.forEach(n => {
       const [nx, ny] = g2s(n.x, n.y);
       const color = n.risk > 0.8 ? "#f04863" : n.risk > 0.5 ? "#f0a848" : "#5a82b4";
       ctx.beginPath();
       ctx.fillStyle = "#0e192b";
       ctx.strokeStyle = color;
       ctx.lineWidth = 1 * s;
       const r = (n.type === "SEATING" ? 10 : 6) * s;
       
       if (n.risk > 0.8 && !isRight) {
           ctx.fillStyle = "rgba(240,72,99,0.3)";
           ctx.arc(nx, ny, r * 2.5, 0, Math.PI*2);
           ctx.fill();
           ctx.beginPath();
       }
       
       if (n.control_state === "BLOCK" && isRight) {
           ctx.strokeStyle = "#33d8ff";
           ctx.lineWidth = 2 * s;
           ctx.arc(nx, ny, r * 1.5, 0, Math.PI*2);
           ctx.stroke();
           ctx.beginPath();
       }
       
       ctx.arc(nx, ny, r, 0, Math.PI*2);
       ctx.fill(); ctx.stroke();
    });

    // particles
    ctx.fillStyle = "rgba(51,216,255,0.7)";
    sim.particles.forEach(p => {
       const [px, py] = g2s(p.x, p.y);
       ctx.beginPath();
       ctx.arc(px, py, 1.5 * s, 0, Math.PI*2);
       ctx.fill();
    });
    
    // UI text
    ctx.fillStyle = "rgba(255,255,255,0.5)";
    ctx.font = `${11 * dpr}px monospace`;
    ctx.fillText(isRight ? "WITH AI INTERVENTION" : "WITHOUT AI INTERVENTION", 8 * dpr, 16 * dpr);
  }


})();
