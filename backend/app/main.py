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
import time
import uuid
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, db
from .simulation import SimulationEngine

app = FastAPI(title="CrowdShield AI", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/cameras", StaticFiles(directory="data/cameras"), name="cameras")

# ---------------------------------------------------------------------------
# Global simulation state
# ---------------------------------------------------------------------------
_connections: list[WebSocket] = []
_run_id = str(uuid.uuid4())[:8]


def _on_event(event: dict):
    try:
        db.log_risk_event(_run_id, event.get("tick", 0), event)
    except Exception:
        pass
    _broadcast_soon({"type": "event", **event})


def _on_state(state: dict):
    if state["tick"] % 20 == 0:
        try:
            db.log_snapshot_batch(_run_id, state["tick"], state["timestamp"], state["graph"]["nodes"])
        except Exception:
            pass
    _broadcast_soon(state)


_broadcast_queue: "asyncio.Queue" = None


def _broadcast_soon(payload: dict):
    if _broadcast_queue is not None:
        try:
            _broadcast_queue.put_nowait(payload)
        except Exception:
            pass


sim = SimulationEngine(on_event=_on_event, on_state=_on_state)


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
    sim.running = True
    asyncio.create_task(_simulation_loop())
    asyncio.create_task(_broadcast_loop())


@app.on_event("shutdown")
async def shutdown():
    db.end_run(_run_id)


async def _simulation_loop():
    while True:
        if sim.running:
            sim.step()
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
        await websocket.send_text(json.dumps(sim.snapshot()))
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
    return {"running": sim.running}


@app.post("/api/simulation/stop")
def stop_sim():
    sim.running = False
    return {"running": sim.running}


@app.post("/api/simulation/reset")
def reset_sim():
    sim.reset()
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


class ControlActionRequest(BaseModel):
    checkpoint_id: str
    action: str
    duration_sec: float = 18.0
    reason: str = "Manual operator override"


@app.post("/api/control/action")
def manual_control_action(req: ControlActionRequest):
    node = sim.sg.nodes.get(req.checkpoint_id)
    if not node:
        return {"ok": False, "error": "checkpoint not found"}
    node.control_state = req.action
    node.control_reason = req.reason
    node.control_expires_tick = sim.tick_count + int(req.duration_sec / config.SIMULATION_DT)
    sim._log_event("CHECKPOINT_ACTION", f"[MANUAL] {req.checkpoint_id} {req.action}", {"manual": True})
    return {"ok": True}


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
    return {"status": "ok", "tick": sim.tick_count, "agents": len(sim.agents), "running": sim.running}
