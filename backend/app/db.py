"""
CrowdShield AI - Database Layer
==================================
SQLite is used for the prototype (per spec: "SQLite for prototype unless
PostgreSQL is genuinely useful" - it isn't, for a single-process demo).

Stores: venues, nodes, edges, cameras, crowd snapshots, predictions,
risk events, interventions, and simulation runs. The live simulation state
itself lives in memory (SimulationEngine) for performance; this layer is
for persistence/audit/analytics history, written asynchronously from the
control loop so it never blocks the physics tick.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from typing import Optional

DB_PATH = os.environ.get("CROWDSHIELD_DB_PATH", os.path.join(os.path.dirname(__file__), "..", "crowdshield.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS venues (
    id TEXT PRIMARY KEY, name TEXT, created_at REAL
);
CREATE TABLE IF NOT EXISTS nodes (
    id TEXT PRIMARY KEY, venue_id TEXT, name TEXT, type TEXT,
    x REAL, y REAL, capacity REAL, camera_id TEXT
);
CREATE TABLE IF NOT EXISTS edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT, venue_id TEXT,
    source TEXT, target TEXT, length REAL, width REAL, capacity REAL
);
CREATE TABLE IF NOT EXISTS cameras (
    id TEXT PRIMARY KEY, checkpoint_id TEXT, filename TEXT
);
CREATE TABLE IF NOT EXISTS crowd_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, tick INTEGER,
    timestamp REAL, node_id TEXT, people REAL, density REAL, risk REAL
);
CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, tick INTEGER,
    node_id TEXT, horizon INTEGER, predicted_density REAL
);
CREATE TABLE IF NOT EXISTS risk_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, tick INTEGER,
    timestamp REAL, event_type TEXT, message TEXT, payload TEXT
);
CREATE TABLE IF NOT EXISTS interventions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, tick INTEGER,
    timestamp REAL, risk_location TEXT, intervention_location TEXT,
    action TEXT, confidence REAL, risk_without REAL, risk_with REAL, reason TEXT
);
CREATE TABLE IF NOT EXISTS simulation_runs (
    id TEXT PRIMARY KEY, started_at REAL, ended_at REAL, seed INTEGER, scenario TEXT
);
"""


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)


def seed_from_graph(sg, venue_id: str = "stadium-01", venue_name: str = "CrowdShield Demo Stadium"):
    with get_conn() as conn:
        conn.execute("INSERT OR REPLACE INTO venues VALUES (?, ?, ?)", (venue_id, venue_name, time.time()))
        conn.execute("DELETE FROM nodes WHERE venue_id = ?", (venue_id,))
        conn.execute("DELETE FROM edges WHERE venue_id = ?", (venue_id,))
        for n in sg.nodes.values():
            conn.execute(
                "INSERT INTO nodes (id, venue_id, name, type, x, y, capacity, camera_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (n.id, venue_id, n.name, n.type, n.x, n.y, n.capacity, n.camera_id),
            )
        for e in sg.edges.values():
            conn.execute(
                "INSERT INTO edges (venue_id, source, target, length, width, capacity) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (venue_id, e.source, e.target, e.length, e.width, e.capacity),
            )
        conn.execute("DELETE FROM cameras")
        from . import config
        for cam_id, cp_id in config.CAMERA_CHECKPOINT_MAP.items():
            conn.execute("INSERT OR REPLACE INTO cameras VALUES (?, ?, ?)",
                         (cam_id, cp_id, config.CAMERA_FILENAMES.get(cam_id, "")))


def start_run(run_id: str, seed: int, scenario: str = "default"):
    with get_conn() as conn:
        conn.execute("INSERT OR REPLACE INTO simulation_runs VALUES (?, ?, ?, ?, ?)",
                     (run_id, time.time(), None, seed, scenario))


def end_run(run_id: str):
    with get_conn() as conn:
        conn.execute("UPDATE simulation_runs SET ended_at = ? WHERE id = ?", (time.time(), run_id))


def log_risk_event(run_id: str, tick: int, event: dict):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO risk_events (run_id, tick, timestamp, event_type, message, payload) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, tick, time.time(), event.get("type"), event.get("message"),
             json.dumps(event.get("payload", {}))),
        )


def log_intervention(run_id: str, tick: int, decision: dict):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO interventions (run_id, tick, timestamp, risk_location, intervention_location, "
            "action, confidence, risk_without, risk_with, reason) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (run_id, tick, time.time(), decision.get("risk_location"),
             decision.get("intervention_location"), decision.get("action"),
             decision.get("confidence"), decision.get("risk_without_action"),
             decision.get("risk_with_action"), decision.get("reason")),
        )


def log_snapshot_batch(run_id: str, tick: int, timestamp: float, nodes: list):
    with get_conn() as conn:
        conn.executemany(
            "INSERT INTO crowd_snapshots (run_id, tick, timestamp, node_id, people, density, risk) "
            "VALUES (?,?,?,?,?,?,?)",
            [(run_id, tick, timestamp, n["id"], n["current_people"], n["current_density"], n["risk"])
             for n in nodes],
        )
