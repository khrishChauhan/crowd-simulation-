"""
CrowdShield AI - FastAPI Backend
===================================
Exposes REST endpoints for one-shot queries plus a WebSocket for live state
pushes. The simulation runs on a background asyncio task independent of any
client connection (per spec: "simulation must run independently of the
video pipeline" and continuously, not only while someone is watching).

Run:
    uvicorn app.main:app --reload --port 8000
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from typing import Optional

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel

from . import config, db
from .graph_model import build_temple_graph, build_metro_graph, build_stadium_graph
from .simulation import SimulationEngine

app = FastAPI(title="CrowdShield AI", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
_cameras_dir = "data/cameras" if os.path.isdir("data/cameras") else os.path.join(os.path.dirname(__file__), "..", "..", "data", "cameras")
if os.path.isdir(_cameras_dir):
    app.mount("/cameras", StaticFiles(directory=_cameras_dir), name="cameras")

# ---------------------------------------------------------------------------
# Global simulation state
# ---------------------------------------------------------------------------
_connections: list[WebSocket] = []
_run_id = str(uuid.uuid4())[:8]


def _make_event_handler(arena_id: str):
    def _on_event(event: dict):
        try:
            db.log_risk_event(_run_id, event.get("tick", 0), event)
        except Exception:
            pass
        _broadcast_soon({"type": "event", "arena_id": arena_id, **event})
    return _on_event


def _make_state_handler(arena_id: str):
    def _on_state(state: dict):
        if state["tick"] % 20 == 0:
            try:
                db.log_snapshot_batch(_run_id, state["tick"], state["timestamp"], state["graph"]["nodes"])
            except Exception:
                pass
        _broadcast_soon(state)
    return _on_state


_broadcast_queue: "asyncio.Queue" = None


def _broadcast_soon(payload: dict):
    if _broadcast_queue is not None:
        try:
            _broadcast_queue.put_nowait(payload)
        except Exception:
            pass


# ---- Dual-arena engines ----
player_sim = SimulationEngine(
    on_event=_make_event_handler("player"),
    on_state=_make_state_handler("player"),
    seed=config.DEMO_RANDOM_SEED,        # 42
    arena_id="player",
    target_evacuation=500,
)
ai_sim = SimulationEngine(
    on_event=_make_event_handler("ai"),
    on_state=_make_state_handler("ai"),
    seed=config.DEMO_RANDOM_SEED + 100,  # 142
    arena_id="ai",
    target_evacuation=500,
)
# Backward-compat alias: existing REST endpoints target the player arena
sim = player_sim


# ---------------------------------------------------------------------------
# Startup: init DB, seed data, start background loops
# ---------------------------------------------------------------------------
@app.on_event("startup")
async def startup():
    global _broadcast_queue
    _broadcast_queue = asyncio.Queue()
    db.init_db()
    db.seed_from_graph(sim.sg)
    db.start_run(_run_id, config.DEMO_RANDOM_SEED, "hackathon_demo")
    player_sim.running = True
    ai_sim.running = True
    asyncio.create_task(_simulation_loop())
    asyncio.create_task(_broadcast_loop())


@app.on_event("shutdown")
async def shutdown():
    db.end_run(_run_id)


async def _simulation_loop():
    while True:
        if player_sim.running:
            player_sim.step()
        if ai_sim.running:
            ai_sim.step()
        if player_sim.running or ai_sim.running:
            _broadcast_soon({
                "type": "game_state",
                "player": player_sim.snapshot(),
                "ai": ai_sim.snapshot(),
            })
        await asyncio.sleep(config.SIMULATION_DT)


async def _broadcast_loop():
    while True:
        payload = await _broadcast_queue.get()
        dead = []
        for ws in _connections:
            try:
                await ws.send_text(json.dumps(payload))
            except Exception:
                dead.append(ws)
        for ws in dead:
            if ws in _connections:
                _connections.remove(ws)


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------
@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    _connections.append(websocket)
    try:
        await websocket.send_text(json.dumps({
            "type": "game_state",
            "player": player_sim.snapshot(),
            "ai": ai_sim.snapshot(),
        }))
        while True:
            await websocket.receive_text()  # keep-alive / ignore inbound pings
    except WebSocketDisconnect:
        pass
    finally:
        if websocket in _connections:
            _connections.remove(websocket)


# ---------------------------------------------------------------------------
# REST - read endpoints
# ---------------------------------------------------------------------------
@app.get("/api/stadium")
def get_stadium():
    return sim.sg.to_dict()


@app.get("/api/cameras")
def get_cameras():
    return {k: v.to_dict() for k, v in sim.cameras.observe_all(sim.sg).items()}


@app.get("/api/checkpoints")
def get_checkpoints():
    return [n.to_dict() for n in sim.sg.nodes.values() if n.type == "CHECKPOINT"]


@app.get("/api/checkpoints/{checkpoint_id}")
def get_checkpoint_detail(checkpoint_id: str):
    node = sim.sg.nodes.get(checkpoint_id)
    if not node:
        return {"error": "not found"}
    from .risk_engine import time_to_critical_seconds, critical_people_for_node
    ttc = time_to_critical_seconds(node, critical_people_for_node(node))
    return {
        **node.to_dict(),
        "time_to_critical_sec": None if ttc == float("inf") else round(ttc, 1),
    }


@app.get("/api/crowd-state")
def get_crowd_state():
    return sim.snapshot()


@app.get("/api/predictions")
def get_predictions(horizon: int = 30):
    return {nid: n.predicted_density.get(horizon) for nid, n in sim.sg.nodes.items()}


@app.get("/api/predictions/ghosts")
def get_ghosts(horizon: int = 30):
    return sim.predicted_snapshot(horizon)


@app.get("/api/risks")
def get_risks():
    return {nid: {"risk": n.risk, "breakdown": n.risk_breakdown} for nid, n in sim.sg.nodes.items()}


@app.get("/api/interventions")
def get_interventions():
    return sim.decisions_log[-20:]


@app.get("/api/analytics")
def get_analytics():
    return {"history": sim.metrics_history[-120:], "events": sim.events[-100:]}


@app.get("/api/events")
def get_events(limit: int = 50):
    return sim.events[-limit:]


# ---------------------------------------------------------------------------
# REST - control endpoints
# ---------------------------------------------------------------------------
@app.post("/api/simulation/start")
def start_sim():
    sim.running = True
    ai_sim.running = True
    return {"running": sim.running}


@app.post("/api/simulation/stop")
def stop_sim():
    sim.running = False
    ai_sim.running = False
    return {"running": sim.running}


@app.post("/api/simulation/reset")
def reset_sim():
    sim.reset()
    ai_sim.reset()
    return {"ok": True}


class ScenarioRequest(BaseModel):
    scenario: str
    params: Optional[dict] = None


@app.post("/api/scenario/start")
def start_scenario(req: ScenarioRequest):
    p = req.params or {}
    if req.scenario == "crowd_surge":
        sim.inject_crowd_surge(p.get("gate", "GATE-01"), int(p.get("count", 70)))
    elif req.scenario == "gate_failure":
        sim.close_exit(p.get("exit", "EXIT-03"))
    elif req.scenario == "reopen_exit":
        sim.reopen_exit(p.get("exit", "EXIT-03"))
    elif req.scenario == "counterflow":
        sim.trigger_counterflow()
    elif req.scenario == "camera_failure":
        sim.toggle_camera_offline(p.get("camera_id", "CAM-03"), True)
    elif req.scenario == "camera_restore":
        sim.toggle_camera_offline(p.get("camera_id", "CAM-03"), False)
    else:
        return {"ok": False, "error": f"unknown scenario {req.scenario}"}
    return {"ok": True, "scenario": req.scenario}


def _is_exit_reachable_from_seating(sg: StadiumGraph) -> bool:
    unblocked_exits = {nid for nid, n in sg.nodes.items() if n.type == "EXIT" and n.control_state != "BLOCK"}
    if not unblocked_exits:
        return False
    seating_nodes = [nid for nid, n in sg.nodes.items() if n.type == "SEATING" and n.control_state != "BLOCK"]
    if not seating_nodes:
        return True
    for seat in seating_nodes:
        visited = set()
        queue = [seat]
        can_reach = False
        while queue:
            curr = queue.pop(0)
            if curr in unblocked_exits:
                can_reach = True
                break
            if curr in visited:
                continue
            visited.add(curr)
            for nbr in sg.neighbors(curr):
                if nbr not in visited:
                    edge = sg.edges.get((curr, nbr))
                    nbr_node = sg.nodes.get(nbr)
                    if edge and edge.enabled and edge.control_state != "BLOCK" and nbr_node and nbr_node.control_state != "BLOCK":
                        queue.append(nbr)
        if not can_reach:
            return False
    return True


class ControlActionRequest(BaseModel):
    arena: Optional[str] = "player"
    checkpoint_id: str
    action: str
    duration_sec: float = 30.0
    reason: str = "Tactile map command"


@app.post("/api/control/action")
def manual_control_action(req: ControlActionRequest):
    engine = player_sim if req.arena == "player" else ai_sim
    node = engine.sg.nodes.get(req.checkpoint_id)
    if not node:
        return {"ok": False, "error": "checkpoint not found"}

    if req.action == "BLOCK":
        prev_state = node.control_state
        node.control_state = "BLOCK"
        if not _is_exit_reachable_from_seating(engine.sg):
            node.control_state = prev_state
            return {"ok": False, "error": "CANNOT DISCONNECT ALL EXITS"}
    else:
        node.control_state = req.action

    node.control_reason = req.reason
    node.control_expires_tick = engine.tick_count + int(req.duration_sec / config.SIMULATION_DT)
    engine._log_event("CHECKPOINT_ACTION", f"[TACTILE] {req.checkpoint_id} {req.action}", {"node": req.checkpoint_id, "action": req.action, "manual": True})
    return {"ok": True, "node": req.checkpoint_id, "action": req.action}


class CorridorControlRequest(BaseModel):
    arena: Optional[str] = "player"
    source: str
    target: str
    state: str  # "BIDIRECTIONAL", "FORWARD_ONLY", "REVERSE_ONLY", "BLOCK"


@app.post("/api/control/corridor")
def control_corridor(req: CorridorControlRequest):
    engine = player_sim if req.arena == "player" else ai_sim
    e_fwd = engine.sg.edges.get((req.source, req.target))
    e_rev = engine.sg.edges.get((req.target, req.source))

    if e_fwd is None and e_rev is None:
        return {"ok": False, "error": "corridor not found"}

    prev_fwd = (e_fwd.enabled, e_fwd.control_state, e_fwd.direction) if e_fwd else None
    prev_rev = (e_rev.enabled, e_rev.control_state, e_rev.direction) if e_rev else None

    if req.state == "BIDIRECTIONAL":
        if e_fwd:
            e_fwd.enabled = True
            e_fwd.control_state = "NORMAL"
            e_fwd.direction = "BIDIRECTIONAL"
        if e_rev:
            e_rev.enabled = True
            e_rev.control_state = "NORMAL"
            e_rev.direction = "BIDIRECTIONAL"
    elif req.state == "FORWARD_ONLY":
        if e_fwd:
            e_fwd.enabled = True
            e_fwd.control_state = "NORMAL"
            e_fwd.direction = "FORWARD_ONLY"
        if e_rev:
            e_rev.enabled = False
            e_rev.control_state = "BLOCK"
            e_rev.direction = "FORWARD_ONLY"
    elif req.state == "REVERSE_ONLY":
        if e_fwd:
            e_fwd.enabled = False
            e_fwd.control_state = "BLOCK"
            e_fwd.direction = "REVERSE_ONLY"
        if e_rev:
            e_rev.enabled = True
            e_rev.control_state = "NORMAL"
            e_rev.direction = "REVERSE_ONLY"
    elif req.state == "BLOCK":
        if e_fwd:
            e_fwd.enabled = False
            e_fwd.control_state = "BLOCK"
            e_fwd.direction = "BLOCK"
        if e_rev:
            e_rev.enabled = False
            e_rev.control_state = "BLOCK"
            e_rev.direction = "BLOCK"
    else:
        return {"ok": False, "error": f"unknown state {req.state}"}

    if not _is_exit_reachable_from_seating(engine.sg):
        if e_fwd and prev_fwd:
            e_fwd.enabled, e_fwd.control_state, e_fwd.direction = prev_fwd
        if e_rev and prev_rev:
            e_rev.enabled, e_rev.control_state, e_rev.direction = prev_rev
        return {"ok": False, "error": "CANNOT DISCONNECT ALL EXITS"}

    engine._log_event(
        "CORRIDOR_ACTION",
        f"[TACTILE] Corridor {req.source} <-> {req.target} set to {req.state}",
        {"source": req.source, "target": req.target, "state": req.state}
    )
    return {"ok": True, "source": req.source, "target": req.target, "state": req.state}


class EmergencyRequest(BaseModel):
    active: bool
    responder_start: Optional[str] = None
    responder_destination: Optional[str] = None


@app.post("/api/emergency")
def emergency(req: EmergencyRequest):
    sim.set_emergency(req.active)
    corridor = None
    if req.active and req.responder_start and req.responder_destination:
        corridor = sim.emergency_corridor(req.responder_start, req.responder_destination)
    return {"ok": True, "active": req.active, "corridor": corridor}


class ShadowModeRequest(BaseModel):
    enabled: bool


@app.post("/api/shadow-mode")
def shadow_mode(req: ShadowModeRequest):
    sim.shadow_mode = req.enabled
    return {"ok": True, "shadow_mode": sim.shadow_mode}


class CameraMappingRequest(BaseModel):
    mapping: dict


@app.post("/api/cameras/mapping")
def set_camera_mapping(req: CameraMappingRequest):
    sim.cameras.set_mapping(req.mapping)
    return {"ok": True, "mapping": sim.cameras.mapping}


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "level": _current_level,
        "player": {
            "tick": player_sim.tick_count,
            "agents": len(player_sim.agents),
            "running": player_sim.running,
            "evacuated": player_sim.evacuated_count,
            "target": player_sim.target_evacuation,
            "penalty_seconds": round(player_sim.penalty_seconds, 1),
            "incident_count": player_sim.incident_count,
            "difficulty": player_sim.difficulty,
        },
        "ai": {
            "tick": ai_sim.tick_count,
            "agents": len(ai_sim.agents),
            "running": ai_sim.running,
            "evacuated": ai_sim.evacuated_count,
            "target": ai_sim.target_evacuation,
            "penalty_seconds": round(ai_sim.penalty_seconds, 1),
            "incident_count": ai_sim.incident_count,
            "difficulty": ai_sim.difficulty,
        },
    }


# ---------------------------------------------------------------------------
# Game Level & Difficulty Configuration
# ---------------------------------------------------------------------------
class LevelConfigRequest(BaseModel):
    level: int = 1
    target_evacuation: Optional[int] = None
    time_limit_sec: Optional[int] = None
    start_immediately: bool = True


LEVEL_CONFIGS = {
    1: {
        "level": 1,
        "name": "Level 1 - Rookie Challenge",
        "difficulty": "novice",
        "target_evacuation": 200,
        "time_limit_sec": 150,
        "description": "Novice AI with sluggish ~5s reaction and basic HOLD controls.",
    },
    2: {
        "level": 2,
        "name": "Level 2 - Pro Clash",
        "difficulty": "pro",
        "target_evacuation": 300,
        "time_limit_sec": 180,
        "description": "Pro AI with fast 2s reaction, full redirect toolkit, and min-cost flow splitting.",
    },
    3: {
        "level": 3,
        "name": "Level 3 - Master Overdrive",
        "difficulty": "super_predictive",
        "target_evacuation": 600,
        "time_limit_sec": 210,
        "description": "Super-Predictive AI anticipating bottlenecks at +30s with proactive gate interventions.",
    },
}

_current_level: int = 1


@app.post("/api/game/level")
def set_game_level(req: LevelConfigRequest):
    """Configure game level: sets AI difficulty, evacuation target, and resets both arenas."""
    global _current_level
    level_num = req.level if req.level in LEVEL_CONFIGS else 1
    cfg = LEVEL_CONFIGS[level_num]
    _current_level = level_num

    target = req.target_evacuation if req.target_evacuation is not None else cfg["target_evacuation"]
    time_limit = req.time_limit_sec if req.time_limit_sec is not None else cfg.get("time_limit_sec", 180)

    # Configure AI difficulty tier
    ai_sim.difficulty = cfg["difficulty"]

    # Configure evacuation targets
    player_sim.target_evacuation = target
    ai_sim.target_evacuation = target

    # Set level and rebuild graph topology
    player_sim.set_level(level_num)
    ai_sim.set_level(level_num)
    player_sim.running = req.start_immediately
    ai_sim.running = req.start_immediately

    return {
        "ok": True,
        "level": level_num,
        "config": cfg,
        "ai_difficulty": ai_sim.difficulty,
        "target_evacuation": target,
        "time_limit_sec": time_limit,
    }


@app.get("/api/game/level")
def get_game_level():
    """Return active game level configuration and available tiers."""
    return {
        "current_level": _current_level,
        "config": LEVEL_CONFIGS.get(_current_level, LEVEL_CONFIGS[1]),
        "available_levels": list(LEVEL_CONFIGS.values()),
    }


# ---------------------------------------------------------------------------
# Dual-arena specific endpoints
# ---------------------------------------------------------------------------
@app.get("/api/crowd-state/dual")
def get_dual_crowd_state():
    """Return snapshots for both player and AI arenas simultaneously."""
    return {
        "player": player_sim.snapshot(),
        "ai": ai_sim.snapshot(),
    }


@app.get("/api/arena/{arena}/state")
def get_arena_state(arena: str):
    """Return snapshot for a specific arena ('player' or 'ai')."""
    engine = player_sim if arena == "player" else ai_sim
    return engine.snapshot()


@app.post("/api/arena/{arena}/simulation/start")
def start_arena_sim(arena: str):
    engine = player_sim if arena == "player" else ai_sim
    engine.running = True
    return {"arena": arena, "running": engine.running}


@app.post("/api/arena/{arena}/simulation/stop")
def stop_arena_sim(arena: str):
    engine = player_sim if arena == "player" else ai_sim
    engine.running = False
    return {"arena": arena, "running": engine.running}


@app.post("/api/arena/{arena}/simulation/reset")
def reset_arena_sim(arena: str):
    engine = player_sim if arena == "player" else ai_sim
    engine.reset()
    return {"arena": arena, "ok": True}


_frontend_dir = os.path.join(os.path.dirname(__file__), "..", "..", "frontend")
_maps_dir = os.path.join(os.path.dirname(__file__), "..", "maps")
_custom_map_path = os.path.join(_maps_dir, "custom_level.json")

# ---------------------------------------------------------------------------
# Admin Map Editor Routes (Multi-Level API)
# ---------------------------------------------------------------------------
@app.get("/admin")
async def serve_admin(request: Request):
    """Serve the visual map editor tool."""
    admin_html = os.path.join(_frontend_dir, "admin.html")
    return FileResponse(admin_html)



@app.get("/api/admin/template/{level:int}")
async def get_admin_template(level: int):
    """Preload template route returning the default graph topology for the given level."""
    if level == 1:
        sg = build_temple_graph()
    elif level == 2:
        sg = build_metro_graph()
    else:
        sg = build_stadium_graph()
    return sg.to_dict()


@app.get("/api/admin/map/{level:int}")
async def get_admin_map_by_level(level: int):
    """Return the saved custom map JSON for a specific level, or 404/custom:false if none exists."""
    level_path = os.path.join(_maps_dir, f"level_{level}.json")
    if not os.path.exists(level_path):
        # Backward-compatibility fallback for level 1
        if level == 1 and os.path.exists(_custom_map_path):
            with open(_custom_map_path, encoding="utf-8") as f:
                data = json.load(f)
                data["custom"] = True
                data["level"] = 1
                return data
        return JSONResponse(
            status_code=404,
            content={"custom": False, "level": level, "error": f"No custom map for level {level}"}
        )
    with open(level_path, encoding="utf-8") as f:
        data = json.load(f)
        data["custom"] = True
        data["level"] = level
        return data


class AdminMapPayload(BaseModel):
    nodes: list
    edges: list
    crates: list = []
    barriers: list = []
    backgroundImage: Optional[str] = None


@app.post("/api/admin/upload-bg/{level:int}")
async def upload_admin_background(level: int, file: UploadFile = File(...)):
    """Upload custom background map image for a given level and save into frontend/."""
    os.makedirs(_frontend_dir, exist_ok=True)
    filename = f"custom_bg_level_{level}.png"
    target_path = os.path.join(_frontend_dir, filename)
    contents = await file.read()
    with open(target_path, "wb") as f:
        f.write(contents)
    return {"ok": True, "path": filename}


@app.post("/api/admin/map/{level:int}")
async def save_admin_map_by_level(level: int, payload: AdminMapPayload):
    """Persist custom map state to backend/maps/level_{level}.json."""
    os.makedirs(_maps_dir, exist_ok=True)
    level_path = os.path.join(_maps_dir, f"level_{level}.json")
    data = payload.model_dump()
    data["custom"] = True
    data["level"] = level
    with open(level_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    # If saving Level 1, also keep custom_level.json in sync for backward compatibility
    if level == 1:
        with open(_custom_map_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    return {"ok": True, "level": level, "path": level_path, "nodes": len(data["nodes"]), "edges": len(data["edges"])}


# Backward-compatibility routes for legacy callers
@app.get("/api/admin/map")
async def get_admin_map():
    return await get_admin_map_by_level(1)


@app.post("/api/admin/map")
async def save_admin_map(payload: AdminMapPayload):
    return await save_admin_map_by_level(1, payload)


# ---------------------------------------------------------------------------
# Frontend page routes (must come before the catch-all static mount)
# ---------------------------------------------------------------------------
@app.api_route("/map", methods=["GET", "HEAD"])
def get_map_page():
    return FileResponse(os.path.join(_frontend_dir, "index.html"))

@app.get("/level1.png")
def get_level1_png():
    return FileResponse(os.path.join(_frontend_dir, "level1.png"))

@app.api_route("/level{lvl:int}", methods=["GET", "HEAD"])
def get_level_page(lvl: int):
    return FileResponse(os.path.join(_frontend_dir, "index.html"))

if os.path.isdir(_frontend_dir):
    app.mount("/", StaticFiles(directory=_frontend_dir, html=True), name="frontend")
