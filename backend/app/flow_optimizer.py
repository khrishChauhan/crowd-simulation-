"""
CrowdShield AI - Flow Optimizer
=================================
Capacity-aware distribution of an incoming flow across multiple downstream
routes. Uses NetworkX's min-cost-flow when the graph slice is well-formed,
falling back to a closed-form capacity-proportional split (still respects
capacity constraints) if the min-cost-flow problem is infeasible for the
current partial demand - this keeps the demo running even on odd graph
states instead of throwing.
"""
from __future__ import annotations

from typing import Dict, List

import networkx as nx

from .graph_model import StadiumGraph


def proportional_split(total_flow: float, route_capacities: Dict[str, float]) -> Dict[str, float]:
    """Distributes `total_flow` across routes proportionally to remaining capacity."""
    total_cap = sum(route_capacities.values())
    if total_cap <= 0:
        return {k: 0.0 for k in route_capacities}
    raw = {k: total_flow * (cap / total_cap) for k, cap in route_capacities.items()}
    # Clip any route above its own capacity and redistribute the remainder once.
    overflow = 0.0
    result = {}
    for k, v in raw.items():
        cap = route_capacities[k]
        if v > cap:
            overflow += v - cap
            result[k] = cap
        else:
            result[k] = v
    if overflow > 0:
        headroom = {k: route_capacities[k] - result[k] for k in result if route_capacities[k] - result[k] > 0}
        total_headroom = sum(headroom.values())
        if total_headroom > 0:
            for k, h in headroom.items():
                result[k] += overflow * (h / total_headroom)
    return result


def min_cost_flow_split(sg: StadiumGraph, source: str, sinks: List[str], demand: float) -> Dict[str, float]:
    """
    Attempts a min-cost-flow based split of `demand` units from `source` to the
    given `sinks`, respecting edge capacities and preferring lower-risk edges
    (cost = 1 + risk*5). Falls back to proportional_split on any infeasibility.
    """
    try:
        H = nx.DiGraph()
        for (s, t), e in sg.edges.items():
            if not e.enabled or e.control_state == "BLOCK":
                continue
            cap = max(1, int(round(e.capacity)))
            cost = max(1, int(round((1 + e.risk * 5) * 10)))
            H.add_edge(s, t, capacity=cap, weight=cost)

        super_sink = "__SINK__"
        for sink in sinks:
            if sink in H:
                H.add_edge(sink, super_sink, capacity=int(round(demand)), weight=0)

        H.nodes[source]["demand"] = -int(round(demand))
        H.nodes[super_sink]["demand"] = int(round(demand))
        for n in H.nodes:
            if n not in (source, super_sink):
                H.nodes[n].setdefault("demand", 0)

        flow_dict = nx.min_cost_flow(H)
        result = {sink: 0.0 for sink in sinks}
        for sink in sinks:
            if sink in flow_dict and super_sink in flow_dict[sink]:
                result[sink] = float(flow_dict[sink][super_sink])
        return result
    except Exception:
        caps = {sink: sg.nodes[sink].capacity for sink in sinks if sink in sg.nodes}
        return proportional_split(demand, caps)
