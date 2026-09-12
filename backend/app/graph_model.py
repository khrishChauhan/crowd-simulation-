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


# ---------------------------------------------------------------------------
# Backward-compatibility alias map for tests & legacy scripts
# ---------------------------------------------------------------------------
ALIAS_MAP = {
    "GATE-01": "ENTRY-01",
    "GATE-02": "ENTRY-02",
    "GATE-03": "ENTRY-03",
    "GATE-04": "ENTRY-02",
    "CP-01": "HUB-01",
    "CP-02": "HUB-02",
    "CP-03": "HUB-03",
    "CP-04": "HUB-04",
    "CP-05": "HUB-03",
    "CP-06": "HUB-04",
    "CP-07": "HUB-02",
    "CP-08": "HUB-01",
    "CP-09": "HUB-02",
    "CP-10": "HUB-03",
    "SEATING-A": "PLAZA-WEST",
    "SEATING-B": "PLAZA-CENTER",
    "SEATING-C": "PLAZA-EAST",
    "SEATING-D": "PLAZA-CENTER",
    "JCT-01": "JUNCTION-01",
    "JCT-02": "JUNCTION-02",
    "JCT-03": "JUNCTION-03",
    "JCT-04": "JUNCTION-04",
    "EXIT-04": "EXIT-03",
}


class NodeDict(dict):
    def __getitem__(self, key):
        canonical = ALIAS_MAP.get(key, key)
        return super().__getitem__(canonical)

    def __contains__(self, key):
        canonical = ALIAS_MAP.get(key, key)
        return super().__contains__(canonical)

    def get(self, key, default=None):
        canonical = ALIAS_MAP.get(key, key)
        return super().get(canonical, default)


class EdgeDict(dict):
    def _canonical_key(self, key):
        if isinstance(key, tuple) and len(key) == 2:
            return (ALIAS_MAP.get(key[0], key[0]), ALIAS_MAP.get(key[1], key[1]))
        return key

    def __getitem__(self, key):
        return super().__getitem__(self._canonical_key(key))

    def __setitem__(self, key, value):
        super().__setitem__(self._canonical_key(key), value)

    def __contains__(self, key):
        return super().__contains__(self._canonical_key(key))

    def get(self, key, default=None):
        return super().get(self._canonical_key(key), default)


class StadiumGraph:
    """Wraps a NetworkX DiGraph with typed Node/Edge convenience objects."""

    def __init__(self):
        self.g = nx.DiGraph()
        self.nodes: Dict[str, Node] = NodeDict()
        self.edges: Dict[tuple, Edge] = EdgeDict()

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
        canonical = ALIAS_MAP.get(node_id, node_id)
        return list(self.g.successors(canonical))

    def predecessors(self, node_id: str) -> List[str]:
        canonical = ALIAS_MAP.get(node_id, node_id)
        return list(self.g.predecessors(canonical))

    def out_edges(self, node_id: str) -> List[Edge]:
        canonical = ALIAS_MAP.get(node_id, node_id)
        return [self.edges[(canonical, t)] for t in self.g.successors(canonical)
                if (canonical, t) in self.edges]

    def in_edges(self, node_id: str) -> List[Edge]:
        canonical = ALIAS_MAP.get(node_id, node_id)
        return [self.edges[(s, canonical)] for s in self.g.predecessors(canonical)
                if (s, canonical) in self.edges]

    def to_dict(self) -> dict:
        return {
            "nodes": [n.to_dict() for n in self.nodes.values()],
            "edges": [e.to_dict() for e in self.edges.values()],
        }


# ---------------------------------------------------------------------------
# Stadium layout builder - Top-to-Bottom Flow Map (1000 x 700)
# ---------------------------------------------------------------------------
def build_stadium_graph() -> StadiumGraph:
    """
    Builds the Top-to-Bottom venue flow topology:
      - Top row (y=80): 3 Entrance Gates (ENTRY-01, ENTRY-02, ENTRY-03)
      - Upper hub (y=220): 4 concourse checkpoints (HUB-01..HUB-04)
      - Midfield (y=360): 3 central plazas (PLAZA-WEST, PLAZA-CENTER, PLAZA-EAST) & 2 bypasses (BYPASS-LEFT, BYPASS-RIGHT)
      - Lower funnel (y=500): 4 routing checkpoints (JUNCTION-01..JUNCTION-04)
      - Bottom row (y=620): Exactly 3 Exits (EXIT-01, EXIT-02, EXIT-03)
      - Emergency hub (y=30): EMG-01
    Canvas coordinate space: 1000 x 700.
    """
    sg = StadiumGraph()

    # 1. Top Entrance Row (y = 80)
    top_entries = [
        ("ENTRY-01", "Entry 1", 220.0, 80.0),
        ("ENTRY-02", "Entry 2", 500.0, 80.0),
        ("ENTRY-03", "Entry 3", 780.0, 80.0),
    ]
    for gid, name, x, y in top_entries:
        sg.add_node(Node(gid, name, x, y, "GATE",
                         config.DEFAULT_CAPACITY["GATE"], config.NODE_AREA_M2["GATE"]))

    # 2. Upper Distribution Hub (y = 220)
    hubs = [
        ("HUB-01", "Hub 01", 200.0, 220.0, "CAM-01"),
        ("HUB-02", "Hub 02", 400.0, 220.0, "CAM-02"),
        ("HUB-03", "Hub 03", 600.0, 220.0, "CAM-03"),
        ("HUB-04", "Hub 04", 800.0, 220.0, "CAM-04"),
    ]
    for hid, name, x, y, cam in hubs:
        sg.add_node(Node(hid, name, x, y, "CHECKPOINT",
                         config.DEFAULT_CAPACITY["CHECKPOINT"], config.NODE_AREA_M2["CHECKPOINT"],
                         camera_id=cam))

    # 3. Midfield Plaza & Choke Points (y = 360)
    # Bypasses
    sg.add_node(Node("BYPASS-LEFT", "Bypass Left", 120.0, 360.0, "CHECKPOINT",
                     config.DEFAULT_CAPACITY["CHECKPOINT"], config.NODE_AREA_M2["CHECKPOINT"]))
    sg.add_node(Node("BYPASS-RIGHT", "Bypass Right", 880.0, 360.0, "CHECKPOINT",
                     config.DEFAULT_CAPACITY["CHECKPOINT"], config.NODE_AREA_M2["CHECKPOINT"]))

    # Plaza (Seating / Dwell event area)
    plazas = [
        ("PLAZA-WEST", "West Plaza", 340.0, 360.0, None),
        ("PLAZA-CENTER", "Center Plaza", 500.0, 360.0, "CAM-05"),
        ("PLAZA-EAST", "East Plaza", 660.0, 360.0, None),
    ]
    for pid, name, x, y, cam in plazas:
        sg.add_node(Node(pid, name, x, y, "SEATING",
                         config.DEFAULT_CAPACITY["SEATING"] * 6, config.NODE_AREA_M2["SEATING"],
                         camera_id=cam))

    # 4. Lower Funnel Concourse (y = 500)
    junctions = [
        ("JUNCTION-01", "Junction 01", 220.0, 500.0),
        ("JUNCTION-02", "Junction 02", 420.0, 500.0),
        ("JUNCTION-03", "Junction 03", 580.0, 500.0),
        ("JUNCTION-04", "Junction 04", 780.0, 500.0),
    ]
    for jid, name, x, y in junctions:
        sg.add_node(Node(jid, name, x, y, "JUNCTION",
                         config.DEFAULT_CAPACITY["JUNCTION"], config.NODE_AREA_M2["JUNCTION"]))

    # 5. Bottom Exit Row (y = 620)
    exits = [
        ("EXIT-01", "Exit 01", 220.0, 620.0),
        ("EXIT-02", "Exit 02", 500.0, 620.0),
        ("EXIT-03", "Exit 03", 780.0, 620.0),
    ]
    for eid, name, x, y in exits:
        sg.add_node(Node(eid, name, x, y, "EXIT",
                         config.DEFAULT_CAPACITY["EXIT"], config.NODE_AREA_M2["EXIT"]))

    # 6. Emergency Marshalling Point (y = 30)
    sg.add_node(Node("EMG-01", "Emergency Point", 500.0, 30.0, "EMERGENCY",
                     config.DEFAULT_CAPACITY["EMERGENCY"], config.NODE_AREA_M2["EMERGENCY"]))

    # =========================== EDGES ===========================
    def add(a: str, b: str, width: float = 4.0, cap: float = 8.0, direction: str = "BIDIRECTIONAL"):
        na, nb = sg.nodes[a], sg.nodes[b]
        length = max(8.0, math.hypot(na.x - nb.x, na.y - nb.y) / 4.0)
        sg.add_edge(Edge(a, b, length=length, width=width, capacity=cap, direction=direction))

    # Top Entries -> Upper Hubs
    add("ENTRY-01", "HUB-01")
    add("ENTRY-01", "HUB-02")
    add("ENTRY-02", "HUB-02")
    add("ENTRY-02", "HUB-03")
    add("ENTRY-03", "HUB-03")
    add("ENTRY-03", "HUB-04")

    # Upper Hubs Lateral Cross-Connectors (diversion paths)
    add("HUB-01", "HUB-02")
    add("HUB-02", "HUB-03")
    add("HUB-03", "HUB-04")

    # Upper Hubs -> Midfield Plaza & Bypasses
    add("HUB-01", "BYPASS-LEFT")
    add("HUB-01", "PLAZA-WEST")
    add("HUB-02", "PLAZA-WEST")
    add("HUB-02", "PLAZA-CENTER")
    add("HUB-03", "PLAZA-CENTER")
    add("HUB-03", "PLAZA-EAST")
    add("HUB-04", "PLAZA-EAST")
    add("HUB-04", "BYPASS-RIGHT")

    # Midfield Lateral Cross-Connectors
    add("BYPASS-LEFT", "PLAZA-WEST")
    add("PLAZA-WEST", "PLAZA-CENTER")
    add("PLAZA-CENTER", "PLAZA-EAST")
    add("PLAZA-EAST", "BYPASS-RIGHT")

    # Midfield -> Lower Funnel Junctions
    add("BYPASS-LEFT", "JUNCTION-01")
    add("PLAZA-WEST", "JUNCTION-01")
    add("PLAZA-WEST", "JUNCTION-02")
    add("PLAZA-CENTER", "JUNCTION-02")
    add("PLAZA-CENTER", "JUNCTION-03")
    add("PLAZA-EAST", "JUNCTION-03")
    add("PLAZA-EAST", "JUNCTION-04")
    add("BYPASS-RIGHT", "JUNCTION-04")

    # Lower Junction Lateral Links (lateral rerouting across exit funnels)
    add("JUNCTION-01", "JUNCTION-02")
    add("JUNCTION-02", "JUNCTION-03")
    add("JUNCTION-03", "JUNCTION-04")

    # Lower Junctions -> Bottom Exits
    add("JUNCTION-01", "EXIT-01")
    add("JUNCTION-02", "EXIT-01")
    add("JUNCTION-02", "EXIT-02")
    add("JUNCTION-03", "EXIT-02")
    add("JUNCTION-03", "EXIT-03")
    add("JUNCTION-04", "EXIT-03")

    # Emergency Corridor
    add("ENTRY-01", "EMG-01", cap=4.0)
    add("ENTRY-02", "EMG-01", cap=4.0)
    add("EMG-01", "HUB-02", cap=4.0)
    add("EMG-01", "EXIT-01", cap=4.0)

    return sg


def build_temple_graph() -> StadiumGraph:
    sg = StadiumGraph()
    def n(nid, name, x, y, ntype="CHECKPOINT", cap=config.DEFAULT_CAPACITY["CHECKPOINT"]):
        sg.add_node(Node(nid, name, float(x), float(y), ntype, cap, config.NODE_AREA_M2.get(ntype, 10.0)))
    # Entries and Exit — calibrated to stone gate arches
    n("ENTRY-01", "West Gate",  305,  95, "GATE")
    n("ENTRY-02", "East Gate",  975,  95, "GATE")
    n("EXIT-01",  "Temple Exit", 640, 762, "EXIT", config.DEFAULT_CAPACITY["EXIT"] * 3)
    # Top Hubs & Upper Cross-Corridor
    n("H_L1", "West Upper Split", 270, 185)
    n("H_R1", "East Upper Split", 975, 185)
    n("T_L1", "West Top Link 1",  400, 185)
    n("T_L2", "West Top Link 2",  400, 118)
    n("T_C",  "Center Top Shrine", 640, 118)
    n("T_R2", "East Top Link 2",  840, 118)
    n("T_R1", "East Top Link 1",  840, 185)
    # Center Downward Spine
    n("C_MID", "Center Mid Shrine", 640, 250)
    n("C_L1",  "Center Left 1",    505, 250)
    n("C_R1",  "Center Right 1",   775, 250)
    n("C_L2",  "Center Left 2",    505, 452)
    n("C_R2",  "Center Right 2",   775, 452)
    n("C_L3",  "Center Left 3",    505, 632, "JUNCTION")
    n("C_R3",  "Center Right 3",   775, 632, "JUNCTION")
    n("C_L4",  "Center Left 4",    505, 762)
    n("C_R4",  "Center Right 4",   775, 762)
    # Left Split Paths (Barrier/Diverter node)
    n("B_L",  "West Barrier Node", 270, 390, "JUNCTION")
    n("I_L1", "West Inner 1",      270, 452)
    n("I_L2", "West Inner 2",      400, 452, "JUNCTION")
    n("I_L3", "West Inner 3",      400, 632)
    n("O_L1", "West Outer 1",      145, 390)
    n("O_L2", "West Outer 2",      145, 510)
    n("O_L3", "West Outer 3",      215, 510)
    n("O_L4", "West Outer 4",      215, 632)
    n("O_L5", "West Outer 5",      305, 632)
    n("O_L6", "West Outer 6",      305, 720)
    n("O_L7", "West Outer 7",      475, 720)
    n("O_L8", "West Outer 8",      475, 762)
    # Right Split Paths (Barrier/Diverter node)
    n("B_R",  "East Barrier Node", 975, 390, "JUNCTION")
    n("I_R1", "East Inner 1",      975, 452)
    n("I_R2", "East Inner 2",      840, 452, "JUNCTION")
    n("I_R3", "East Inner 3",      840, 632)
    n("O_R1", "East Outer 1",     1120, 390)
    n("O_R2", "East Outer 2",     1120, 510)
    n("O_R3", "East Outer 3",     1055, 510)
    n("O_R4", "East Outer 4",     1055, 632)
    n("O_R5", "East Outer 5",      975, 632)
    n("O_R6", "East Outer 6",      975, 720)
    n("O_R7", "East Outer 7",      805, 720)
    n("O_R8", "East Outer 8",      805, 762)
    # Emergency Node
    n("EMG-01", "Sanctuary", 640, 40, "EMERGENCY")

    def add(a: str, b: str, cap: float = 12.0):
        if a in sg.nodes and b in sg.nodes:
            na, nb = sg.nodes[a], sg.nodes[b]
            length = max(8.0, math.hypot(na.x - nb.x, na.y - nb.y) / 4.0)
            sg.add_edge(Edge(a, b, length=length, width=5.0, capacity=cap))
    # Top entries & upper loop
    add("ENTRY-01", "H_L1")
    add("ENTRY-02", "H_R1")
    add("H_L1", "T_L1");  add("T_L1", "T_L2");  add("T_L2", "T_C")
    add("H_R1", "T_R1");  add("T_R1", "T_R2");  add("T_R2", "T_C")
    add("T_C",  "C_MID")
    # Central spine
    add("C_MID", "C_L1");  add("C_MID", "C_R1")
    add("C_L1",  "C_L2");  add("C_R1",  "C_R2")
    add("C_L2",  "C_L3");  add("C_R2",  "C_R3")
    add("C_L3",  "C_L4");  add("C_R3",  "C_R4")
    add("C_L4",  "EXIT-01"); add("C_R4", "EXIT-01")
    # Left split (B_L is the diverter junction)
    add("H_L1", "B_L")
    add("B_L",  "I_L1");  add("B_L", "O_L1")
    add("I_L1", "I_L2");  add("I_L2", "C_L2");  add("I_L2", "I_L3")
    add("I_L3", "C_L3")
    add("O_L1", "O_L2");  add("O_L2", "O_L3");  add("O_L3", "O_L4")
    add("O_L4", "O_L5");  add("O_L5", "O_L6");  add("O_L6", "O_L7")
    add("O_L7", "O_L8");  add("O_L8", "EXIT-01")
    # Right split (B_R is the diverter junction)
    add("H_R1", "B_R")
    add("B_R",  "I_R1");  add("B_R", "O_R1")
    add("I_R1", "I_R2");  add("I_R2", "C_R2");  add("I_R2", "I_R3")
    add("I_R3", "C_R3")
    add("O_R1", "O_R2");  add("O_R2", "O_R3");  add("O_R3", "O_R4")
    add("O_R4", "O_R5");  add("O_R5", "O_R6");  add("O_R6", "O_R7")
    add("O_R7", "O_R8");  add("O_R8", "EXIT-01")
    # Emergency
    add("ENTRY-01", "EMG-01", cap=5.0)
    add("ENTRY-02", "EMG-01", cap=5.0)
    add("EMG-01",   "T_C",    cap=5.0)
    return sg


def build_metro_graph() -> StadiumGraph:
    """Build Metro station graph for Level 2."""
    return build_stadium_graph()


def build_graph_for_level(level: int = 1) -> StadiumGraph:
    """Return the correct graph for the given level.

    Before falling back to hardcoded graphs, checks if backend/maps/level_{level}.json exists.
    If so, instantiates and builds the custom graph from that JSON file.
    """
    import os as _os
    _custom_level = _os.path.join(_os.path.dirname(__file__), "..", "maps", f"level_{level}.json")
    if _os.path.exists(_custom_level):
        return build_custom_graph(_custom_level)

    # Legacy custom_level.json check for level 1
    if level == 1:
        _legacy = _os.path.join(_os.path.dirname(__file__), "..", "maps", "custom_level.json")
        if _os.path.exists(_legacy):
            return build_custom_graph(_legacy)
        return build_temple_graph()
    elif level == 2:
        return build_metro_graph()
    return build_stadium_graph()


def build_custom_graph(filepath: str) -> StadiumGraph:
    """Build a StadiumGraph from a JSON file produced by the Admin Map Editor.

    JSON schema::
        {
          "nodes": [{"id", "name", "x", "y", "type"}, ...],
          "edges": [{"source", "target", "capacity"?}, ...],
          "crates":   [...],   # informational only — not used by the graph
          "barriers": [...],   # informational only — not used by the graph
        }
    """
    import json as _json
    with open(filepath, encoding="utf-8") as f:
        data = _json.load(f)

    sg = StadiumGraph()

    for nd in data.get("nodes", []):
        ntype = nd.get("type", "CHECKPOINT")
        sg.add_node(Node(
            id=nd["id"],
            name=nd.get("name", nd["id"]),
            x=float(nd["x"]),
            y=float(nd["y"]),
            type=ntype,
            capacity=config.DEFAULT_CAPACITY.get(ntype, config.DEFAULT_CAPACITY["CHECKPOINT"]),
            area_m2=config.NODE_AREA_M2.get(ntype, 10.0),
        ))

    for ed in data.get("edges", []):
        src, tgt = ed["source"], ed["target"]
        if src not in sg.nodes or tgt not in sg.nodes:
            continue
        na, nb = sg.nodes[src], sg.nodes[tgt]
        length = max(8.0, math.hypot(na.x - nb.x, na.y - nb.y) / 4.0)
        cap = float(ed.get("capacity", 12.0))
        sg.add_edge(Edge(src, tgt, length=length, width=5.0, capacity=cap))

    return sg


NODE_TYPE_COLORS = {
    "GATE": "#22d3ee",
    "CHECKPOINT": "#60a5fa",
    "JUNCTION": "#a78bfa",
    "SEATING": "#fbbf24",
    "EXIT": "#10b981",
    "EMERGENCY": "#f87171",
}

