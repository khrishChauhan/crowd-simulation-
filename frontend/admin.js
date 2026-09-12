/**
 * CrowdShield Map Architect — Visual Canvas Editor Engine
 * Multi-Level Support (Levels 1, 2, 3) with Preload Templates, Auto-Loading,
 * and Streamlined "Save to Server" / "Reset to Default" Actions.
 */

(() => {
  "use strict";

  // ---------------------------------------------------------------------------
  // Constants & Level Assets
  // ---------------------------------------------------------------------------
  const NATIVE_WIDTH = 1280;
  const NATIVE_HEIGHT = 853;

  const LEVEL_BACKGROUNDS = {
    1: "level1.png",
    2: "map-bg.jpg",
    3: "map-bg.jpg",
  };

  const NODE_COLORS = {
    GATE: "#10b981",       // Green
    EXIT: "#ef4444",       // Red
    CHECKPOINT: "#60a5fa", // Cyan / Gray-blue
    JUNCTION: "#a78bfa",   // Purple / Blue
    EMERGENCY: "#f43f5e",  // Magenta
    SEATING: "#fbbf24",    // Amber
  };

  // Default tactical interactive objects per level
  const DEFAULT_TACTICAL_OBJECTS = {
    1: {
      crates: [
        { id: "BOX-01", name: "West Pass Crate", x: 270, y: 285, edge: ["H_L1", "B_L"] },
        { id: "BOX-02", name: "East Pass Crate", x: 975, y: 285, edge: ["H_R1", "B_R"] },
        { id: "BOX-03", name: "Center Altar Crate", x: 640, y: 200, edge: ["T_C", "C_MID"] },
      ],
      barriers: [
        { id: "BARRIER-01", name: "West Diverter", x: 270, y: 390, straightEdge: ["B_L", "I_L1"], divertEdge: ["B_L", "O_L1"] },
        { id: "BARRIER-02", name: "East Diverter", x: 975, y: 390, straightEdge: ["B_R", "I_R1"], divertEdge: ["B_R", "O_R1"] },
      ],
    },
    2: {
      crates: [],
      barriers: [],
    },
    3: {
      crates: [],
      barriers: [],
    },
  };

  // ---------------------------------------------------------------------------
  // Editor State
  // ---------------------------------------------------------------------------
  const state = {
    currentLevel: 1,
    nodes: [],
    edges: [],
    crates: [],
    barriers: [],
    backgroundImage: "level1.png",
    bgOpacity: 0.85,
    showGrid: true,
    isCustomMap: false,

    // UI & Viewport
    mode: "select", // "select" | "node" | "edge" | "crate" | "barrier" | "delete"
    scale: 1.0,
    panX: 0,
    panY: 0,

    // Selections & Interactions
    selectedElement: null,
    draggingNode: null,
    isPanning: false,
    panStartX: 0,
    panStartY: 0,

    // State machines for multi-step interactions
    pendingEdgeSource: null,
    pendingBarrierNode: null,
    pendingBarrierStraight: null,
    hoveredItem: null,

    mouseWorldX: 0,
    mouseWorldY: 0,
  };

  // Background Image object
  const bgImg = new Image();
  bgImg.crossOrigin = "anonymous";
  let bgLoaded = false;
  bgImg.onload = () => { bgLoaded = true; fitViewToMap(); };
  bgImg.onerror = () => { bgLoaded = false; };

  // DOM Elements
  let canvas, ctx, container;
  let coordsDisplay, statNodes, statEdges, statCrates, statBarriers;
  let modeHint, toastEl, selectionInspector, levelSelect, levelStatusText, lnkPlayLevel, hdrPlayLvlNum;

  // ---------------------------------------------------------------------------
  // Coordinate Helpers
  // ---------------------------------------------------------------------------
  function screenToWorld(sx, sy) {
    return {
      x: (sx - state.panX) / state.scale,
      y: (sy - state.panY) / state.scale,
    };
  }

  function worldToScreen(wx, wy) {
    return {
      x: wx * state.scale + state.panX,
      y: wy * state.scale + state.panY,
    };
  }

  function dist(x1, y1, x2, y2) {
    return Math.hypot(x2 - x1, y2 - y1);
  }

  function distToSegment(px, py, x1, y1, x2, y2) {
    const l2 = (x2 - x1) ** 2 + (y2 - y1) ** 2;
    if (l2 === 0) return dist(px, py, x1, y1);
    let t = ((px - x1) * (x2 - x1) + (py - y1) * (y2 - y1)) / l2;
    t = Math.max(0, Math.min(1, t));
    return dist(px, py, x1 + t * (x2 - x1), y1 + t * (y2 - y1));
  }

  // ---------------------------------------------------------------------------
  // Hit Testing
  // ---------------------------------------------------------------------------
  function findNodeAt(wx, wy, radius = 14) {
    for (let i = state.nodes.length - 1; i >= 0; i--) {
      const n = state.nodes[i];
      if (dist(wx, wy, n.x, n.y) <= radius) {
        return n;
      }
    }
    return null;
  }

  function findCrateAt(wx, wy, size = 16) {
    for (let i = state.crates.length - 1; i >= 0; i--) {
      const c = state.crates[i];
      if (Math.abs(wx - c.x) <= size && Math.abs(wy - c.y) <= size) {
        return c;
      }
    }
    return null;
  }

  function findBarrierAt(wx, wy, radius = 18) {
    for (let i = state.barriers.length - 1; i >= 0; i--) {
      const b = state.barriers[i];
      if (dist(wx, wy, b.x, b.y) <= radius) {
        return b;
      }
    }
    return null;
  }

  function findEdgeAt(wx, wy, threshold = 8) {
    const nodeMap = new Map(state.nodes.map(n => [n.id, n]));
    for (let i = state.edges.length - 1; i >= 0; i--) {
      const e = state.edges[i];
      const u = nodeMap.get(e.source);
      const v = nodeMap.get(e.target);
      if (!u || !v) continue;
      const d = distToSegment(wx, wy, u.x, u.y, v.x, v.y);
      if (d <= threshold) {
        return e;
      }
    }
    return null;
  }

  function hitTestAll(wx, wy) {
    const node = findNodeAt(wx, wy);
    if (node) return { type: "node", data: node };

    const crate = findCrateAt(wx, wy);
    if (crate) return { type: "crate", data: crate };

    const barrier = findBarrierAt(wx, wy);
    if (barrier) return { type: "barrier", data: barrier };

    const edge = findEdgeAt(wx, wy);
    if (edge) return { type: "edge", data: edge };

    return null;
  }

  // ---------------------------------------------------------------------------
  // Viewport Manipulation & UI Updates
  // ---------------------------------------------------------------------------
  function fitViewToMap() {
    if (!container) return;
    const cw = container.clientWidth;
    const ch = container.clientHeight;
    const padding = 20;

    const scaleX = (cw - padding * 2) / NATIVE_WIDTH;
    const scaleY = (ch - padding * 2) / NATIVE_HEIGHT;
    state.scale = Math.min(scaleX, scaleY, 1.5);

    state.panX = (cw - NATIVE_WIDTH * state.scale) / 2;
    state.panY = (ch - NATIVE_HEIGHT * state.scale) / 2;
    updateCoordsDisplay();
  }

  function updateCoordsDisplay() {
    if (coordsDisplay) {
      const pct = Math.round(state.scale * 100);
      coordsDisplay.textContent = `X: ${Math.round(state.mouseWorldX)} | Y: ${Math.round(state.mouseWorldY)} (Zoom: ${pct}%)`;
    }
  }

  function updateStats() {
    if (statNodes) statNodes.textContent = state.nodes.length;
    if (statEdges) statEdges.textContent = state.edges.length;
    if (statCrates) statCrates.textContent = state.crates.length;
    if (statBarriers) statBarriers.textContent = state.barriers.length;
  }

  function updateInspector() {
    if (!selectionInspector) return;
    if (!state.selectedElement) {
      selectionInspector.innerHTML = `<span class="text-dim">Select an element to inspect</span>`;
      return;
    }

    const { type, data } = state.selectedElement;
    if (type === "node") {
      selectionInspector.innerHTML = `
        <div class="row"><strong>Node:</strong> <span>${data.id}</span></div>
        <div class="row"><span class="text-dim">Name:</span> <span>${data.name || data.id}</span></div>
        <div class="row"><span class="text-dim">Type:</span> <span style="color:${NODE_COLORS[data.type] || '#fff'}">${data.type}</span></div>
        <div class="row"><span class="text-dim">Pos:</span> <span>(${Math.round(data.x)}, ${Math.round(data.y)})</span></div>
      `;
    } else if (type === "edge") {
      selectionInspector.innerHTML = `
        <div class="row"><strong>Edge:</strong> <span>${data.source} → ${data.target}</span></div>
      `;
    } else if (type === "crate") {
      selectionInspector.innerHTML = `
        <div class="row"><strong>Crate:</strong> <span>${data.id}</span></div>
        <div class="row"><span class="text-dim">Edge:</span> <span>${data.edge ? data.edge.join(" ↔ ") : "None"}</span></div>
        <div class="row"><span class="text-dim">Pos:</span> <span>(${Math.round(data.x)}, ${Math.round(data.y)})</span></div>
      `;
    } else if (type === "barrier") {
      selectionInspector.innerHTML = `
        <div class="row"><strong>Barrier:</strong> <span>${data.id}</span></div>
        <div class="row"><span class="text-dim">Straight:</span> <span>${data.straightEdge ? data.straightEdge.join(" → ") : "None"}</span></div>
        <div class="row"><span class="text-dim">Divert:</span> <span>${data.divertEdge ? data.divertEdge.join(" → ") : "None"}</span></div>
      `;
    }
  }

  function updateStatusPill(isCustom) {
    state.isCustomMap = isCustom;
    if (levelStatusText) {
      levelStatusText.textContent = isCustom ? "Custom Saved Map" : "Preload Template";
      levelStatusText.className = "status-badge" + (isCustom ? " custom" : "");
    }
  }

  function showToast(msg, type = "normal") {
    if (!toastEl) return;
    toastEl.textContent = msg;
    toastEl.className = "editor-toast" + (type === "success" ? " success" : type === "error" ? " error" : "");
    toastEl.hidden = false;
    clearTimeout(toastEl._timer);
    toastEl._timer = setTimeout(() => { toastEl.hidden = true; }, 3200);
  }

  // ---------------------------------------------------------------------------
  // Canvas Rendering Engine
  // ---------------------------------------------------------------------------
  function render() {
    if (!canvas || !ctx) return;

    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    ctx.translate(state.panX, state.panY);
    ctx.scale(state.scale, state.scale);

    // 1. Draw Background Map Image
    if (bgLoaded && bgImg.complete) {
      ctx.save();
      ctx.globalAlpha = state.bgOpacity;
      ctx.drawImage(bgImg, 0, 0, NATIVE_WIDTH, NATIVE_HEIGHT);
      ctx.restore();
    } else {
      ctx.fillStyle = "#0c1017";
      ctx.fillRect(0, 0, NATIVE_WIDTH, NATIVE_HEIGHT);
    }

    // Map Border
    ctx.strokeStyle = "rgba(56, 189, 248, 0.4)";
    ctx.lineWidth = 2 / state.scale;
    ctx.strokeRect(0, 0, NATIVE_WIDTH, NATIVE_HEIGHT);

    // 2. Grid Lines
    if (state.showGrid) {
      ctx.strokeStyle = "rgba(255, 255, 255, 0.04)";
      ctx.lineWidth = 1 / state.scale;
      const step = 50;
      ctx.beginPath();
      for (let x = 0; x <= NATIVE_WIDTH; x += step) {
        ctx.moveTo(x, 0); ctx.lineTo(x, NATIVE_HEIGHT);
      }
      for (let y = 0; y <= NATIVE_HEIGHT; y += step) {
        ctx.moveTo(0, y); ctx.lineTo(NATIVE_WIDTH, y);
      }
      ctx.stroke();
    }

    const nodeMap = new Map(state.nodes.map(n => [n.id, n]));

    // 3. Draw Edges
    for (const edge of state.edges) {
      const u = nodeMap.get(edge.source);
      const v = nodeMap.get(edge.target);
      if (!u || !v) continue;

      const isSelected = state.selectedElement?.type === "edge" && state.selectedElement.data === edge;
      const isHovered = state.hoveredItem?.type === "edge" && state.hoveredItem.data === edge;

      ctx.beginPath();
      ctx.moveTo(u.x, u.y);
      ctx.lineTo(v.x, v.y);

      if (isSelected) {
        ctx.strokeStyle = "#facc15";
        ctx.lineWidth = 4 / state.scale;
      } else if (isHovered) {
        ctx.strokeStyle = "#38bdf8";
        ctx.lineWidth = 3.5 / state.scale;
      } else {
        ctx.strokeStyle = "rgba(148, 163, 184, 0.5)";
        ctx.lineWidth = 2.5 / state.scale;
      }
      ctx.stroke();

      // Flow tick indicator at 70% along edge
      const dx = v.x - u.x;
      const dy = v.y - u.y;
      const angle = Math.atan2(dy, dx);
      const mx = u.x + dx * 0.7;
      const my = u.y + dy * 0.7;
      const arrowSize = 5 / state.scale;

      ctx.beginPath();
      ctx.moveTo(mx - arrowSize * Math.cos(angle - Math.PI / 6), my - arrowSize * Math.sin(angle - Math.PI / 6));
      ctx.lineTo(mx, my);
      ctx.lineTo(mx - arrowSize * Math.cos(angle + Math.PI / 6), my - arrowSize * Math.sin(angle + Math.PI / 6));
      ctx.strokeStyle = isSelected ? "#facc15" : "rgba(255, 255, 255, 0.5)";
      ctx.lineWidth = 1.5 / state.scale;
      ctx.stroke();
    }

    // 4. Pending Edge Preview
    if (state.mode === "edge" && state.pendingEdgeSource) {
      ctx.beginPath();
      ctx.moveTo(state.pendingEdgeSource.x, state.pendingEdgeSource.y);
      ctx.lineTo(state.mouseWorldX, state.mouseWorldY);
      ctx.strokeStyle = "#38bdf8";
      ctx.lineWidth = 2 / state.scale;
      ctx.setLineDash([4 / state.scale, 4 / state.scale]);
      ctx.stroke();
      ctx.setLineDash([]);
    }

    // 5. Draw Crates
    for (const crate of state.crates) {
      const isSelected = state.selectedElement?.type === "crate" && state.selectedElement.data === crate;
      const isHovered = state.hoveredItem?.type === "crate" && state.hoveredItem.data === crate;
      const size = 18 / state.scale;

      ctx.save();
      ctx.translate(crate.x, crate.y);

      ctx.fillStyle = isSelected ? "#eab308" : isHovered ? "#9a3412" : "#78350f";
      ctx.fillRect(-size / 2, -size / 2, size, size);

      ctx.strokeStyle = "#fbbf24";
      ctx.lineWidth = 1.5 / state.scale;
      ctx.strokeRect(-size / 2, -size / 2, size, size);
      ctx.beginPath();
      ctx.moveTo(-size / 2, -size / 2); ctx.lineTo(size / 2, size / 2);
      ctx.moveTo(size / 2, -size / 2); ctx.lineTo(-size / 2, size / 2);
      ctx.stroke();

      ctx.fillStyle = "#fff";
      ctx.font = `${Math.max(9, 10 / state.scale)}px sans-serif`;
      ctx.textAlign = "center";
      ctx.fillText(crate.id, 0, -size / 2 - 4 / state.scale);

      ctx.restore();
    }

    // 6. Draw Barriers
    for (const barrier of state.barriers) {
      const isSelected = state.selectedElement?.type === "barrier" && state.selectedElement.data === barrier;
      const isHovered = state.hoveredItem?.type === "barrier" && state.hoveredItem.data === barrier;
      const r = 16 / state.scale;

      ctx.save();
      ctx.translate(barrier.x, barrier.y);

      ctx.beginPath();
      ctx.arc(0, 0, r, 0, Math.PI * 2);
      ctx.fillStyle = isSelected ? "rgba(245, 158, 11, 0.4)" : isHovered ? "rgba(245, 158, 11, 0.25)" : "rgba(245, 158, 11, 0.15)";
      ctx.fill();
      ctx.strokeStyle = "#f59e0b";
      ctx.lineWidth = 2 / state.scale;
      ctx.stroke();

      ctx.beginPath();
      ctx.moveTo(-r * 0.6, 0);
      ctx.lineTo(0, 0);
      ctx.lineTo(0, r * 0.6);
      ctx.strokeStyle = "#fbbf24";
      ctx.lineWidth = 3 / state.scale;
      ctx.stroke();

      ctx.fillStyle = "#f59e0b";
      ctx.font = `bold ${Math.max(9, 10 / state.scale)}px sans-serif`;
      ctx.textAlign = "center";
      ctx.fillText(barrier.id, 0, -r - 4 / state.scale);

      ctx.restore();
    }

    // 7. Draw Nodes
    for (const node of state.nodes) {
      const isSelected = state.selectedElement?.type === "node" && state.selectedElement.data === node;
      const isHovered = state.hoveredItem?.type === "node" && state.hoveredItem.data === node;
      const isPendingSource = state.pendingEdgeSource === node || state.pendingBarrierNode === node;
      const radius = (node.type === "GATE" || node.type === "EXIT" ? 9 : 7) / state.scale;

      if (isSelected || isPendingSource) {
        ctx.beginPath();
        ctx.arc(node.x, node.y, radius + 6 / state.scale, 0, Math.PI * 2);
        ctx.fillStyle = isPendingSource ? "rgba(56, 189, 248, 0.3)" : "rgba(250, 204, 21, 0.3)";
        ctx.fill();
      }

      ctx.beginPath();
      ctx.arc(node.x, node.y, radius, 0, Math.PI * 2);
      ctx.fillStyle = NODE_COLORS[node.type] || "#60a5fa";
      ctx.fill();
      ctx.strokeStyle = isSelected ? "#facc15" : isHovered ? "#fff" : "rgba(255, 255, 255, 0.7)";
      ctx.lineWidth = 1.5 / state.scale;
      ctx.stroke();

      ctx.fillStyle = "#e2e8f0";
      ctx.font = `${Math.max(9, 10 / state.scale)}px 'JetBrains Mono', monospace`;
      ctx.textAlign = "center";
      ctx.fillText(node.id, node.x, node.y - radius - 3 / state.scale);
    }

    requestAnimationFrame(render);
  }

  // ---------------------------------------------------------------------------
  // User Actions & State Mutation
  // ---------------------------------------------------------------------------
  function addNode(x, y) {
    const typeSelect = document.getElementById("nodeTypeSelect");
    const nameInput = document.getElementById("nodeNameInput");
    const type = typeSelect ? typeSelect.value : "CHECKPOINT";

    const prefix = type === "GATE" ? "ENTRY" : type === "EXIT" ? "EXIT" : type === "JUNCTION" ? "JN" : type === "EMERGENCY" ? "EMG" : "CK";
    const existing = state.nodes.filter(n => n.id.startsWith(prefix));
    const nextNum = existing.length + 1;
    const id = `${prefix}-${String(nextNum).padStart(2, "0")}`;
    const name = nameInput && nameInput.value.trim() ? nameInput.value.trim() : id;

    const node = { id, name, x: Math.round(x), y: Math.round(y), type };
    state.nodes.push(node);
    state.selectedElement = { type: "node", data: node };
    updateStats();
    updateInspector();
    showToast(`Added node: ${id} (${type})`);
  }

  function addEdge(sourceNode, targetNode) {
    if (sourceNode.id === targetNode.id) return;
    const exists = state.edges.some(e =>
      (e.source === sourceNode.id && e.target === targetNode.id) ||
      (e.source === targetNode.id && e.target === sourceNode.id)
    );
    if (exists) {
      showToast("Edge already exists", "error");
      return;
    }
    const edge = { source: sourceNode.id, target: targetNode.id };
    state.edges.push(edge);
    state.selectedElement = { type: "edge", data: edge };
    updateStats();
    updateInspector();
    showToast(`Connected: ${sourceNode.id} ↔ ${targetNode.id}`);
  }

  function addCrateOnEdge(edge) {
    const nodeMap = new Map(state.nodes.map(n => [n.id, n]));
    const u = nodeMap.get(edge.source);
    const v = nodeMap.get(edge.target);
    if (!u || !v) return;

    const mx = Math.round((u.x + v.x) / 2);
    const my = Math.round((u.y + v.y) / 2);

    const nextNum = state.crates.length + 1;
    const id = `BOX-${String(nextNum).padStart(2, "0")}`;

    const crate = {
      id,
      name: `${id} Obstacle`,
      x: mx,
      y: my,
      edge: [edge.source, edge.target],
    };

    state.crates.push(crate);
    state.selectedElement = { type: "crate", data: crate };
    updateStats();
    updateInspector();
    showToast(`Added Crate ${id} on corridor ${edge.source} ↔ ${edge.target}`);
  }

  function addBarrier(node, straightEdge, divertEdge) {
    const nextNum = state.barriers.length + 1;
    const id = `BARRIER-${String(nextNum).padStart(2, "0")}`;

    const barrier = {
      id,
      name: `${id} Diverter`,
      x: node.x,
      y: node.y,
      straightEdge: [straightEdge.source, straightEdge.target],
      divertEdge: [divertEdge.source, divertEdge.target],
    };

    state.barriers.push(barrier);
    state.selectedElement = { type: "barrier", data: barrier };
    updateStats();
    updateInspector();
    showToast(`Added Barrier ${id} at ${node.id}`);
  }

  function deleteElement(hit) {
    if (!hit) return;
    const { type, data } = hit;

    if (type === "node") {
      state.nodes = state.nodes.filter(n => n.id !== data.id);
      state.edges = state.edges.filter(e => e.source !== data.id && e.target !== data.id);
      state.crates = state.crates.filter(c => !c.edge || (c.edge[0] !== data.id && c.edge[1] !== data.id));
      state.barriers = state.barriers.filter(b => b.x !== data.x || b.y !== data.y);
      showToast(`Deleted node ${data.id}`);
    } else if (type === "edge") {
      state.edges = state.edges.filter(e => e !== data);
      state.crates = state.crates.filter(c =>
        !c.edge || !(
          (c.edge[0] === data.source && c.edge[1] === data.target) ||
          (c.edge[0] === data.target && c.edge[1] === data.source)
        )
      );
      showToast(`Deleted edge ${data.source} ↔ ${data.target}`);
    } else if (type === "crate") {
      state.crates = state.crates.filter(c => c !== data);
      showToast(`Deleted crate ${data.id}`);
    } else if (type === "barrier") {
      state.barriers = state.barriers.filter(b => b !== data);
      showToast(`Deleted barrier ${data.id}`);
    }

    state.selectedElement = null;
    updateStats();
    updateInspector();
  }

  // ---------------------------------------------------------------------------
  // Preload & Reset Logic (Multi-Level API)
  // ---------------------------------------------------------------------------
  async function loadLevelMap(level) {
    state.currentLevel = level;
    state.selectedElement = null;
    state.pendingEdgeSource = null;
    state.pendingBarrierNode = null;
    state.pendingBarrierStraight = null;

    // Update background image for this level
    state.backgroundImage = LEVEL_BACKGROUNDS[level] || "level1.png";
    bgImg.src = state.backgroundImage;

    // Update header link
    if (lnkPlayLevel) lnkPlayLevel.href = `/level${level}`;
    if (hdrPlayLvlNum) hdrPlayLvlNum.textContent = String(level);

    try {
      // 1. Try loading custom map for this level
      const res = await fetch(`/api/admin/map/${level}`);
      if (res.ok) {
        const data = await res.json();
        if (data && data.custom !== false && Array.isArray(data.nodes) && data.nodes.length > 0) {
          state.nodes = data.nodes;
          state.edges = data.edges || [];
          state.crates = data.crates || [];
          state.barriers = data.barriers || [];
          if (data.backgroundImage) {
            state.backgroundImage = data.backgroundImage;
            bgImg.src = state.backgroundImage;
          }
          updateStats();
          updateInspector();
          updateStatusPill(true);
          fitViewToMap();
          showToast(`Loaded Custom Map for Level ${level} (${state.nodes.length} nodes)`, "success");
          return;
        }
      }

      // 2. If no custom map, load default template from backend
      await resetToDefaultTemplate(level, false);
    } catch (err) {
      console.warn(`Error loading map for level ${level}, falling back to template:`, err);
      await resetToDefaultTemplate(level, false);
    }
  }

  async function resetToDefaultTemplate(level, notifyUser = true) {
    try {
      showToast(`Fetching default template for Level ${level}...`, "normal");
      const res = await fetch(`/api/admin/template/${level}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();

      // Parse nodes and edges from sg.to_dict()
      state.nodes = (data.nodes || []).map(n => ({
        id: n.id,
        name: n.name || n.id,
        x: Math.round(n.x),
        y: Math.round(n.y),
        type: n.type || "CHECKPOINT",
      }));

      state.edges = (data.edges || []).map(e => ({
        source: e.source,
        target: e.target,
        capacity: e.capacity || 12.0,
      }));

      // Load default tactical objects for this level
      const defTactical = DEFAULT_TACTICAL_OBJECTS[level] || { crates: [], barriers: [] };
      state.crates = JSON.parse(JSON.stringify(defTactical.crates || []));
      state.barriers = JSON.parse(JSON.stringify(defTactical.barriers || []));

      state.backgroundImage = LEVEL_BACKGROUNDS[level] || "level1.png";
      bgImg.src = state.backgroundImage;

      updateStats();
      updateInspector();
      updateStatusPill(false);
      fitViewToMap();

      if (notifyUser) {
        showToast(`Reset Level ${level} to default template!`, "success");
      }
    } catch (err) {
      console.error(`Error loading template for level ${level}:`, err);
      showToast(`Failed to load template: ${err.message}`, "error");
    }
  }

  // ---------------------------------------------------------------------------
  // Save Logic
  // ---------------------------------------------------------------------------
  async function saveToServer() {
    const level = state.currentLevel;
    try {
      const payload = {
        nodes: state.nodes,
        edges: state.edges,
        crates: state.crates,
        barriers: state.barriers,
        backgroundImage: state.backgroundImage,
      };

      const res = await fetch(`/api/admin/map/${level}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      const data = await res.json();
      if (data.ok) {
        updateStatusPill(true);
        showToast(`Saved Level ${level} map to server! (${data.nodes} nodes, ${data.edges} edges)`, "success");
      } else {
        showToast(`Failed to save: ${data.error || "Unknown error"}`, "error");
      }
    } catch (err) {
      console.error(err);
      showToast(`Network error saving map: ${err.message}`, "error");
    }
  }

  // ---------------------------------------------------------------------------
  // Mouse & Mode Event Handling
  // ---------------------------------------------------------------------------
  function setMode(newMode) {
    state.mode = newMode;
    state.pendingEdgeSource = null;
    state.pendingBarrierNode = null;
    state.pendingBarrierStraight = null;

    document.querySelectorAll(".mode-radio-label").forEach(l => {
      l.classList.toggle("active", l.dataset.mode === newMode);
      const radio = l.querySelector("input");
      if (radio) radio.checked = (l.dataset.mode === newMode);
    });

    if (canvas) {
      canvas.className = `mode-${newMode}`;
    }

    if (modeHint) {
      const hints = {
        select: "<strong>Move/Select:</strong> Click and drag nodes to reposition. Click any item to inspect its properties.",
        node: "<strong>Add Node:</strong> Click anywhere on the map to place a new node of the selected type.",
        edge: "<strong>Connect Edges:</strong> Click Node A, then click Node B to draw a corridor between them.",
        crate: "<strong>Add Crate:</strong> Click on any corridor line to place a tactical crate obstacle at its midpoint.",
        barrier: "<strong>Add Barrier:</strong> Step 1: Click a junction node. Step 2: Click straight corridor. Step 3: Click divert corridor.",
        delete: "<strong>Delete:</strong> Click any node, corridor, crate, or barrier to permanently delete it.",
      };
      modeHint.innerHTML = hints[newMode] || "";
    }
  }

  function setupEventListeners() {
    window.addEventListener("resize", () => {
      if (!canvas || !container) return;
      canvas.width = container.clientWidth;
      canvas.height = container.clientHeight;
    });

    // Level Selector Dropdown
    levelSelect?.addEventListener("change", (e) => {
      const lvl = parseInt(e.target.value, 10) || 1;
      loadLevelMap(lvl);
    });

    // Mode Selector radio buttons
    document.querySelectorAll('input[name="editorMode"]').forEach(radio => {
      radio.addEventListener("change", (e) => {
        setMode(e.target.value);
      });
    });

    // Action Buttons
    document.getElementById("btnSaveServer")?.addEventListener("click", saveToServer);
    document.getElementById("btnResetDefault")?.addEventListener("click", () => {
      if (confirm(`Reset Level ${state.currentLevel} to default template? Custom unsaved changes will be lost.`)) {
        resetToDefaultTemplate(state.currentLevel, true);
      }
    });

    // Zoom buttons
    document.getElementById("btnZoomIn")?.addEventListener("click", () => {
      state.scale = Math.min(3.0, state.scale * 1.25);
      updateCoordsDisplay();
    });
    document.getElementById("btnZoomOut")?.addEventListener("click", () => {
      state.scale = Math.max(0.2, state.scale * 0.8);
      updateCoordsDisplay();
    });
    document.getElementById("btnResetView")?.addEventListener("click", fitViewToMap);
    document.getElementById("btnToggleGrid")?.addEventListener("click", (e) => {
      state.showGrid = !state.showGrid;
      e.target.classList.toggle("active", state.showGrid);
    });

    // Canvas Mouse Events
    canvas.addEventListener("mousemove", onMouseMove);
    canvas.addEventListener("mousedown", onMouseDown);
    window.addEventListener("mouseup", onMouseUp);
    canvas.addEventListener("wheel", onWheel, { passive: false });

    // Keyboard shortcuts
    window.addEventListener("keydown", (e) => {
      if (e.target.tagName === "INPUT" || e.target.tagName === "SELECT") return;
      if (e.key === "1") setMode("select");
      else if (e.key === "2") setMode("node");
      else if (e.key === "3") setMode("edge");
      else if (e.key === "4") setMode("crate");
      else if (e.key === "5") setMode("barrier");
      else if (e.key === "6" || e.key === "Delete" || e.key === "Backspace") {
        if (state.selectedElement) {
          deleteElement(state.selectedElement);
        } else {
          setMode("delete");
        }
      }
      else if (e.key === "s" && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        saveToServer();
      }
    });
  }

  function onMouseMove(e) {
    const rect = canvas.getBoundingClientRect();
    const sx = e.clientX - rect.left;
    const sy = e.clientY - rect.top;
    const world = screenToWorld(sx, sy);
    state.mouseWorldX = world.x;
    state.mouseWorldY = world.y;
    updateCoordsDisplay();

    if (state.isPanning) {
      state.panX = sx - state.panStartX;
      state.panY = sy - state.panStartY;
      return;
    }

    if (state.draggingNode) {
      state.draggingNode.x = Math.max(0, Math.min(NATIVE_WIDTH, Math.round(world.x)));
      state.draggingNode.y = Math.max(0, Math.min(NATIVE_HEIGHT, Math.round(world.y)));

      // Dynamically recalculate midpoints for crates connected to this node
      for (const crate of state.crates) {
        if (crate.edge && (crate.edge[0] === state.draggingNode.id || crate.edge[1] === state.draggingNode.id)) {
          const u = state.nodes.find(n => n.id === crate.edge[0]);
          const v = state.nodes.find(n => n.id === crate.edge[1]);
          if (u && v) {
            crate.x = Math.round((u.x + v.x) / 2);
            crate.y = Math.round((u.y + v.y) / 2);
          }
        }
      }

      // Move barriers anchored to this node
      for (const barrier of state.barriers) {
        if (barrier.x === state.draggingNode.x || (dist(barrier.x, barrier.y, state.draggingNode.x, state.draggingNode.y) < 30)) {
          barrier.x = state.draggingNode.x;
          barrier.y = state.draggingNode.y;
        }
      }

      updateInspector();
      return;
    }

    state.hoveredItem = hitTestAll(world.x, world.y);
  }

  function onMouseDown(e) {
    const rect = canvas.getBoundingClientRect();
    const sx = e.clientX - rect.left;
    const sy = e.clientY - rect.top;
    const world = screenToWorld(sx, sy);

    if (e.button === 1 || e.altKey || (e.button === 0 && e.shiftKey)) {
      e.preventDefault();
      state.isPanning = true;
      state.panStartX = sx - state.panX;
      state.panStartY = sy - state.panY;
      canvas.classList.add("panning");
      return;
    }

    if (e.button !== 0) return;

    const hit = hitTestAll(world.x, world.y);

    switch (state.mode) {
      case "select": {
        if (hit && hit.type === "node") {
          state.draggingNode = hit.data;
          state.selectedElement = hit;
          canvas.classList.add("dragging");
        } else {
          state.selectedElement = hit;
          if (!hit) {
            state.isPanning = true;
            state.panStartX = sx - state.panX;
            state.panStartY = sy - state.panY;
            canvas.classList.add("panning");
          }
        }
        updateInspector();
        break;
      }

      case "node": {
        addNode(world.x, world.y);
        break;
      }

      case "edge": {
        const nodeHit = findNodeAt(world.x, world.y);
        if (nodeHit) {
          if (!state.pendingEdgeSource) {
            state.pendingEdgeSource = nodeHit;
            showToast(`Selected source node: ${nodeHit.id}. Now click target node.`);
          } else {
            addEdge(state.pendingEdgeSource, nodeHit);
            state.pendingEdgeSource = null;
          }
        } else {
          state.pendingEdgeSource = null;
        }
        break;
      }

      case "crate": {
        const edgeHit = findEdgeAt(world.x, world.y);
        if (edgeHit) {
          addCrateOnEdge(edgeHit);
        } else {
          showToast("Click directly on a corridor line to add a crate", "normal");
        }
        break;
      }

      case "barrier": {
        if (!state.pendingBarrierNode) {
          const nodeHit = findNodeAt(world.x, world.y);
          if (nodeHit) {
            state.pendingBarrierNode = nodeHit;
            showToast(`Barrier anchor set at: ${nodeHit.id}. Now click straight corridor.`);
          }
          return;
        }

        if (!state.pendingBarrierStraight) {
          const edgeHit = findEdgeAt(world.x, world.y);
          if (edgeHit) {
            state.pendingBarrierStraight = edgeHit;
            showToast("Straight path chosen. Now click diverter corridor.");
          }
          return;
        }

        const divertEdgeHit = findEdgeAt(world.x, world.y);
        if (divertEdgeHit) {
          addBarrier(state.pendingBarrierNode, state.pendingBarrierStraight, divertEdgeHit);
          state.pendingBarrierNode = null;
          state.pendingBarrierStraight = null;
        }
        break;
      }

      case "delete": {
        if (hit) {
          deleteElement(hit);
        }
        break;
      }
    }
  }

  function onMouseUp() {
    if (state.isPanning) {
      state.isPanning = false;
      canvas.classList.remove("panning");
    }
    if (state.draggingNode) {
      state.draggingNode = null;
      canvas.classList.remove("dragging");
    }
  }

  function onWheel(e) {
    e.preventDefault();
    const rect = canvas.getBoundingClientRect();
    const sx = e.clientX - rect.left;
    const sy = e.clientY - rect.top;

    const zoomFactor = e.deltaY < 0 ? 1.15 : 0.85;
    const newScale = Math.max(0.15, Math.min(4.0, state.scale * zoomFactor));

    state.panX = sx - (sx - state.panX) * (newScale / state.scale);
    state.panY = sy - (sy - state.panY) * (newScale / state.scale);
    state.scale = newScale;

    updateCoordsDisplay();
  }

  // ---------------------------------------------------------------------------
  // Initialization
  // ---------------------------------------------------------------------------
  function init() {
    canvas = document.getElementById("editorCanvas");
    container = document.getElementById("canvasContainer");
    coordsDisplay = document.getElementById("coordsDisplay");
    statNodes = document.getElementById("statNodes");
    statEdges = document.getElementById("statEdges");
    statCrates = document.getElementById("statCrates");
    statBarriers = document.getElementById("statBarriers");
    modeHint = document.getElementById("modeHint");
    toastEl = document.getElementById("editorToast");
    selectionInspector = document.getElementById("selectionInspector");
    levelSelect = document.getElementById("levelSelect");
    levelStatusText = document.getElementById("levelStatusText");
    lnkPlayLevel = document.getElementById("lnkPlayLevel");
    hdrPlayLvlNum = document.getElementById("hdrPlayLvlNum");

    if (!canvas || !container) {
      console.error("Editor canvas elements not found!");
      return;
    }

    ctx = canvas.getContext("2d");
    canvas.width = container.clientWidth;
    canvas.height = container.clientHeight;

    setupEventListeners();
    setMode("select");

    // Start with Level 1
    loadLevelMap(1);

    // Start render loop
    requestAnimationFrame(render);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
