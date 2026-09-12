"""
CrowdShield AI - Detectors
============================
Preventive-intelligence detectors that trigger BEFORE density itself
crosses into a dangerous band:

  - Surge detection: sudden growth in inflow at gates.
  - Counterflow detection: significant opposite-direction movement on an edge.
  - Cascade prediction: chained downstream overload following a disruption.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from . import config
from .graph_model import StadiumGraph


def detect_surge(node_id: str, inflow_history: List[float]) -> Optional[dict]:
    if len(inflow_history) < config.SURGE_WINDOW_SAMPLES:
        return None
    window = inflow_history[-config.SURGE_WINDOW_SAMPLES:]
    start, end = window[0], window[-1]
    if start <= 0.5:
        return None
    ratio = end / start
    if ratio >= config.SURGE_INFLOW_GROWTH_RATIO:
        return {
            "node_id": node_id,
            "growth_ratio": round(ratio, 2),
            "inflow_before": round(start, 2),
            "inflow_now": round(end, 2),
            "message": f"SURGE DETECTED at {node_id}: inflow +{round((ratio - 1) * 100)}%",
        }
    return None


def detect_counterflow(sg: StadiumGraph) -> List[dict]:
    alerts = []
    seen = set()
    for (s, t), e in sg.edges.items():
        pair_key = tuple(sorted((s, t)))
        if pair_key in seen:
            continue
        seen.add(pair_key)
        rev = sg.edges.get((t, s))
        if rev is None:
            continue
        fwd_flow, bwd_flow = e.current_flow, rev.current_flow
        total = fwd_flow + bwd_flow
        if total <= 0.5:
            continue
        minority = min(fwd_flow, bwd_flow)
        ratio = minority / total
        e.counterflow_ratio = ratio
        rev.counterflow_ratio = ratio
        if ratio >= config.COUNTERFLOW_RATIO_ALERT:
            alerts.append({
                "edge": f"{s}<->{t}", "counterflow_ratio": round(ratio, 3),
                "message": f"COUNTERFLOW at {s}<->{t}: {round(ratio*100)}% opposing movement",
            })
    return alerts


def predict_cascade(sg: StadiumGraph, origin_node_id: str, max_depth: int = 4) -> List[str]:
    """
    Walks downstream from a disrupted/overloaded node, flagging neighbours
    whose utilization would exceed capacity if they absorbed the origin's
    excess flow. Returns the ordered chain of at-risk node ids.
    """
    chain = [origin_node_id]
    current = origin_node_id
    visited = {origin_node_id}
    excess = max(0.0, sg.nodes[origin_node_id].inflow - sg.nodes[origin_node_id].outflow)

    for _ in range(max_depth):
        neighbors = [n for n in sg.neighbors(current) if n not in visited]
        if not neighbors:
            break
        # pick the neighbour with the least remaining headroom (most likely to overload next)
        candidate = min(neighbors, key=lambda n: sg.nodes[n].capacity - sg.nodes[n].current_people)
        headroom = sg.nodes[candidate].capacity - sg.nodes[candidate].current_people
        if headroom > excess * 3:
            break  # enough slack downstream; cascade unlikely to propagate further
        chain.append(candidate)
        visited.add(candidate)
        current = candidate
        excess *= 0.7  # flow dissipates somewhat at each hop

    return chain if len(chain) > 1 else []
