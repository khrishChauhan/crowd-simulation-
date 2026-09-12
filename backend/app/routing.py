"""
CrowdShield AI - Dynamic Routing
=================================
Implements congestion/risk-aware A* (in practice a Dijkstra variant, since
edge costs already fold in an admissible-ish heuristic via distance) so the
"safest" route may be longer than the geometrically shortest one.

edge_cost = distance + lambda1*congestion + lambda2*predicted_risk
                     + lambda3*utilization + lambda4*travel_time
"""
from __future__ import annotations

import heapq
from typing import Dict, List, Optional

from . import config
from .graph_model import StadiumGraph


def edge_cost(sg: StadiumGraph, source: str, target: str, horizon: int = 15) -> float:
    e = sg.edges.get((source, target))
    if e is None or not e.enabled:
        return float("inf")
    if e.control_state == "BLOCK":
        return float("inf")
    w = config.GRAPH_WEIGHTS
    congestion = min(1.0, e.utilization)
    predicted_risk = e.predicted_flow.get(horizon, e.current_flow) / max(e.capacity, 0.01)
    predicted_risk = min(1.0, predicted_risk)
    cost = (w["distance"] * e.length
            + w["congestion"] * congestion
            + w["predicted_risk"] * predicted_risk
            + w["utilization"] * e.utilization
            + w["travel_time"] * e.travel_time)
    # Soft penalty (not a hard block) for a redirected edge, discouraging but not forbidding.
    if e.control_state in ("HOLD",):
        cost += 25.0
    return cost


def dynamic_astar(sg: StadiumGraph, start: str, goal: str, horizon: int = 15) -> Optional[List[str]]:
    """Dijkstra over dynamic risk-aware costs (heuristic=0 keeps it exact/admissible)."""
    if start not in sg.nodes or goal not in sg.nodes:
        return None
    dist: Dict[str, float] = {start: 0.0}
    prev: Dict[str, str] = {}
    visited = set()
    pq = [(0.0, start)]
    while pq:
        d, u = heapq.heappop(pq)
        if u in visited:
            continue
        visited.add(u)
        if u == goal:
            break
        for v in sg.neighbors(u):
            c = edge_cost(sg, u, v, horizon)
            if c == float("inf"):
                continue
            nd = d + c
            if nd < dist.get(v, float("inf")):
                dist[v] = nd
                prev[v] = u
                heapq.heappush(pq, (nd, v))
    if goal not in dist:
        return None
    path = [goal]
    while path[-1] != start:
        path.append(prev[path[-1]])
    path.reverse()
    return path


def safest_route_exists(sg: StadiumGraph, start: str, goal: str) -> bool:
    return dynamic_astar(sg, start, goal) is not None


def all_destinations_reachable(sg: StadiumGraph, start: str, destinations: List[str]) -> bool:
    """Safety guardrail: verify at least one feasible route remains to every destination."""
    return all(safest_route_exists(sg, start, d) for d in destinations)
