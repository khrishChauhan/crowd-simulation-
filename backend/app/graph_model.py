"""
CrowdShield AI - Stadium Graph Model
=====================================
The stadium is represented as a directed graph G = (V, E) built on top of
NetworkX. Nodes are checkpoints / gates / junctions / seating zones / exits.
Edges are walkable, directional corridors with capacity and live flow state.

This module owns the *static* topology + the *live* mutable state that the
simulation, prediction, risk and intervention modules read/write every tick.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import networkx as nx

from . import config


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class Node:
    id: str
    name: str
    x: float
    y: float
    type: str  # GATE | CHECKPOINT | JUNCTION | SEATING | EXIT | EMERGENCY
    capacity: float
    area_m2: float
    camera_id: Optional[str] = None

    # live state (mutated every simulation tick)
    current_people: float = 0.0
    current_density: float = 0.0       # people / m^2
    predicted_density: Dict[int, float] = field(default_factory=dict)  # horizon -> density
    risk: float = 0.0
    risk_breakdown: Dict[str, float] = field(default_factory=dict)
    control_state: str = "NORMAL"      # NORMAL|HOLD|BLOCK|REDIRECT_LEFT|REDIRECT_RIGHT|OPEN|EMERGENCY
    control_reason: Optional[str] = None
    control_expires_tick: Optional[int] = None
    camera_online: bool = True
    inflow: float = 0.0
    outflow: float = 0.0
    density_history: List[float] = field(default_factory=list)
    inflow_history: List[float] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "x": self.x, "y": self.y,
            "type": self.type, "capacity": self.capacity,
            "current_people": round(self.current_people, 2),
            "current_density": round(self.current_density, 3),
            "predicted_density": {k: round(v, 3) for k, v in self.predicted_density.items()},
            "risk": round(self.risk, 3),
            "risk_breakdown": {k: round(v, 3) for k, v in self.risk_breakdown.items()},
            "control_state": self.control_state,
            "control_reason": self.control_reason,
            "camera_id": self.camera_id,
            "camera_online": self.camera_online,
            "inflow": round(self.inflow, 2),
            "outflow": round(self.outflow, 2),
        }


@dataclass
class Edge:
    source: str
    target: str
    length: float           # meters
    width: float             # meters
    capacity: float          # people/sec
    travel_time: float = 0.0
    current_flow: float = 0.0
    predicted_flow: Dict[int, float] = field(default_factory=dict)
    utilization: float = 0.0
    risk: float = 0.0
    enabled: bool = True
    direction: str = "BIDIRECTIONAL"  # or FORWARD_ONLY
    control_state: str = "NORMAL"
    counterflow_ratio: float = 0.0
    forward_count: int = 0
    backward_count: int = 0

    @property
    def key(self):
        return (self.source, self.target)

    def to_dict(self) -> dict:
        return {
            "source": self.source, "target": self.target, "length": self.length,
            "width": self.width, "capacity": self.capacity,
            "travel_time": round(self.travel_time, 2),
            "current_flow": round(self.current_flow, 2),
            "predicted_flow": {k: round(v, 3) for k, v in self.predicted_flow.items()},
            "utilization": round(self.utilization, 3),
            "risk": round(self.risk, 3),
            "enabled": self.enabled,
            "direction": self.direction,
            "control_state": self.control_state,
            "counterflow_ratio": round(self.counterflow_ratio, 3),
        }


class StadiumGraph:
    """Wraps a NetworkX DiGraph with typed Node/Edge convenience objects."""

    def __init__(self):
        self.g = nx.DiGraph()
        self.nodes: Dict[str, Node] = {}
        self.edges: Dict[tuple, Edge] = {}

    # -- construction -------------------------------------------------
    def add_node(self, node: Node):
        self.nodes[node.id] = node
        self.g.add_node(node.id)

    def add_edge(self, edge: Edge):
        self.edges[edge.key] = edge
        edge.travel_time = edge.length / 1.3  # ~1.3 m/s average walking speed
        self.g.add_edge(edge.source, edge.target)
        if edge.direction == "BIDIRECTIONAL":
            rev = Edge(edge.target, edge.source, edge.length, edge.width,
                       edge.capacity, direction="BIDIRECTIONAL")
            rev.travel_time = edge.travel_time
            self.edges[rev.key] = rev
            self.g.add_edge(rev.source, rev.target)

    def neighbors(self, node_id: str) -> List[str]:
        return list(self.g.successors(node_id))

    def predecessors(self, node_id: str) -> List[str]:
        return list(self.g.predecessors(node_id))

    def out_edges(self, node_id: str) -> List[Edge]:
        return [self.edges[(node_id, t)] for t in self.g.successors(node_id)
                if (node_id, t) in self.edges]

    def in_edges(self, node_id: str) -> List[Edge]:
        return [self.edges[(s, node_id)] for s in self.g.predecessors(node_id)
                if (s, node_id) in self.edges]

    def to_dict(self) -> dict:
        return {
            "nodes": [n.to_dict() for n in self.nodes.values()],
            "edges": [e.to_dict() for e in self.edges.values()],
        }


# ---------------------------------------------------------------------------
# Stadium layout builder - a 2D digital twin with ~26 nodes
# ---------------------------------------------------------------------------
def build_stadium_graph() -> StadiumGraph:
    """
    Builds a large-stadium topology:
      - 4 entrance gates (south side, outer ring)
      - 10 checkpoints forming the concourse ring (5 carry real cameras)
      - 4 junctions connecting concourse to seating bowl quadrants
      - 4 seating zones (A/B/C/D quadrants)
      - 4 exits (north / east / west / emergency-adjacent)
      - 1 emergency marshalling point
    Canvas coordinate space: 1000 x 700.
    """
    sg = StadiumGraph()
    cx, cy = 500, 350  # stadium centre
    outer_r = 300
    concourse_r = 220
    junction_r = 140
    seating_r = 70

    # ---- Gates (south arc, where crowds enter) ----
    gate_angles = [200, 230, 260, 290]  # degrees, roughly southern arc
    gates = []
    for i, ang in enumerate(gate_angles, start=1):
        rad = math.radians(ang)
        x, y = cx + outer_r * math.cos(rad), cy + outer_r * math.sin(rad) * 0.55 + 140
        gid = f"GATE-{i:02d}"
        sg.add_node(Node(gid, f"Gate {i}", x, y, "GATE",
                          config.DEFAULT_CAPACITY["GATE"], config.NODE_AREA_M2["GATE"]))
        gates.append(gid)

    # ---- Checkpoints (concourse ring, full 360) ----
    n_cp = 10
    checkpoints = []
    cam_ids = list(config.CAMERA_CHECKPOINT_MAP.values())
    for i in range(1, n_cp + 1):
        ang = math.radians(360 / n_cp * i - 90)
        x, y = cx + concourse_r * math.cos(ang), cy + concourse_r * math.sin(ang) * 0.7
        cid = f"CP-{i:02d}"
        cam = None
        for cam_key, cp_key in config.CAMERA_CHECKPOINT_MAP.items():
            if cp_key == cid:
                cam = cam_key
        sg.add_node(Node(cid, f"Checkpoint {i:02d}", x, y, "CHECKPOINT",
                          config.DEFAULT_CAPACITY["CHECKPOINT"], config.NODE_AREA_M2["CHECKPOINT"],
                          camera_id=cam))
        checkpoints.append(cid)

    # ---- Junctions (inner ring, 4 quadrant funnels) ----
    junctions = []
    for i, ang in enumerate([45, 135, 225, 315], start=1):
        rad = math.radians(ang)
        x, y = cx + junction_r * math.cos(rad), cy + junction_r * math.sin(rad) * 0.7
        jid = f"JCT-{i:02d}"
        sg.add_node(Node(jid, f"Junction {i:02d}", x, y, "JUNCTION",
                          config.DEFAULT_CAPACITY["JUNCTION"], config.NODE_AREA_M2["JUNCTION"]))
        junctions.append(jid)

    # ---- Seating zones (inner bowl, 4 quadrants) ----
    seating = []
    labels = ["A", "B", "C", "D"]
    for i, (ang, label) in enumerate(zip([45, 135, 225, 315], labels)):
        rad = math.radians(ang)
        x, y = cx + seating_r * math.cos(rad), cy + seating_r * math.sin(rad) * 0.7
        sid = f"SEATING-{label}"
        sg.add_node(Node(sid, f"Seating {label}", x, y, "SEATING",
                          config.DEFAULT_CAPACITY["SEATING"] * 6, config.NODE_AREA_M2["SEATING"]))
        seating.append(sid)

    # ---- Exits (north + east + west + secondary) ----
    exit_defs = [
        ("EXIT-01", 90, 1.0),
        ("EXIT-02", 0, 1.0),
        ("EXIT-03", 180, 1.0),
        ("EXIT-04", 340, 0.9),
    ]
    exits = []
    for eid, ang, rfac in exit_defs:
        rad = math.radians(ang)
        x, y = cx + outer_r * rfac * math.cos(rad), cy + outer_r * rfac * math.sin(rad) * 0.55 - 140
        sg.add_node(Node(eid, eid.replace("-", " "), x, y, "EXIT",
                          config.DEFAULT_CAPACITY["EXIT"], config.NODE_AREA_M2["EXIT"]))
        exits.append(eid)

    # ---- Emergency marshalling point ----
    sg.add_node(Node("EMG-01", "Emergency Corridor Point", cx, cy - outer_r - 40, "EMERGENCY",
                      config.DEFAULT_CAPACITY["EMERGENCY"], config.NODE_AREA_M2["EMERGENCY"]))

    # =========================== EDGES ===========================
    def add(a, b, length=None, width=3.0, cap=6.0, direction="BIDIRECTIONAL"):
        if length is None:
            na, nb = sg.nodes[a], sg.nodes[b]
            length = max(8.0, math.hypot(na.x - nb.x, na.y - nb.y) / 4.0)
        sg.add_edge(Edge(a, b, length=length, width=width, capacity=cap, direction=direction))

    # Gates -> nearest checkpoints (2 gates feed each side of the concourse)
    gate_to_cp = {gates[0]: ["CP-08", "CP-09"], gates[1]: ["CP-09", "CP-10"],
                  gates[2]: ["CP-01", "CP-02"], gates[3]: ["CP-02", "CP-03"]}
    for g, cps in gate_to_cp.items():
        for cp in cps:
            add(g, cp, cap=5.5)

    # Concourse ring: checkpoints connected sequentially (both directions, lateral redirect routes)
    for i in range(n_cp):
        a, b = checkpoints[i], checkpoints[(i + 1) % n_cp]
        add(a, b, cap=6.0)

    # Checkpoints -> junctions (feeding into the bowl)
    cp_to_jct = {
        "CP-01": "JCT-01", "CP-02": "JCT-01", "CP-03": "JCT-02", "CP-04": "JCT-02",
        "CP-05": "JCT-02", "CP-06": "JCT-03", "CP-07": "JCT-03", "CP-08": "JCT-04",
        "CP-09": "JCT-04", "CP-10": "JCT-01",
    }
    for cp, jct in cp_to_jct.items():
        add(cp, jct, cap=6.5)

    # Junctions -> seating zones
    junction_seating = list(zip(junctions, seating))
    for jct, seat in junction_seating:
        add(jct, seat, cap=7.0, width=4.0)

    # Junctions -> exits (egress routes)
    jct_to_exit = {"JCT-01": "EXIT-01", "JCT-02": "EXIT-02", "JCT-03": "EXIT-03", "JCT-04": "EXIT-04"}
    for jct, ex in jct_to_exit.items():
        add(jct, ex, cap=7.5, width=4.5)

    # Cross-junction links (lets the flow optimizer redistribute across exits)
    add("JCT-01", "JCT-02", cap=5.0)
    add("JCT-02", "JCT-03", cap=5.0)
    add("JCT-03", "JCT-04", cap=5.0)
    add("JCT-04", "JCT-01", cap=5.0)

    # Emergency corridor: connects a gate directly to the emergency point and a central checkpoint
    add("GATE-01", "EMG-01", cap=4.0)
    add("EMG-01", "CP-09", cap=4.0)
    add("EMG-01", "EXIT-01", cap=4.0)

    return sg


NODE_TYPE_COLORS = {
    "GATE": "#22d3ee",
    "CHECKPOINT": "#60a5fa",
    "JUNCTION": "#a78bfa",
    "SEATING": "#334155",
    "EXIT": "#34d399",
    "EMERGENCY": "#f87171",
}
