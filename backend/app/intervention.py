"""
CrowdShield AI - Counterfactual Intervention Engine
======================================================
The core AI decision component. When a future bottleneck is predicted:

  1. Identify the RISK LOCATION (the node that will become critical).
  2. Search UPSTREAM nodes with control capability (checkpoints) within a
     reasonable hop distance - the INTERVENTION LOCATION may differ from
     the risk location.
  3. For each candidate (node, action) pair, project the effect forward
     using a lightweight closed-form re-projection (fast enough to run
     every control cycle) and score it with a configurable cost function.
  4. Select the lowest-cost candidate, subject to safety constraints
     (never fully disconnect a destination).

If no safe automatic action exists, returns a NO_SAFE_ACTION decision so
the caller can surface "OPERATOR REVIEW REQUIRED" instead of inventing a
route.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import config
from .graph_model import StadiumGraph
from .risk_engine import node_risk, critical_people_for_node, band_for
from .routing import all_destinations_reachable

CANDIDATE_ACTIONS = [
    "DO_NOTHING", "HOLD", "REDIRECT_LEFT", "REDIRECT_RIGHT",
    "BLOCK_EDGE", "OPEN_EDGE", "SPLIT_FLOW", "CHANGE_DESTINATION_ROUTE",
]

MAX_UPSTREAM_HOPS = 3


@dataclass
class Candidate:
    checkpoint: str
    action: str
    target_edge: Optional[tuple] = None
    projected_risk_location_risk: float = 0.0
    projected_max_density: float = 0.0
    projected_critical_nodes: int = 0
    projected_bottleneck_risk: float = 0.0
    projected_travel_time: float = 0.0
    route_change_penalty: float = 0.0
    congestion_duration: float = 0.0
    cost: float = float("inf")
    reason: str = ""

    def to_dict(self):
        return {
            "checkpoint": self.checkpoint, "action": self.action,
            "cost": round(self.cost, 4),
            "projected_max_density": round(self.projected_max_density, 3),
            "projected_critical_nodes": self.projected_critical_nodes,
            "projected_bottleneck_risk": round(self.projected_bottleneck_risk, 3),
            "projected_travel_time": round(self.projected_travel_time, 2),
            "reason": self.reason,
        }


@dataclass
class Decision:
    risk_location: Optional[str]
    intervention_location: Optional[str]
    action: str
    duration_sec: float
    reason: str
    confidence: float
    risk_without_action: float
    risk_with_action: float
    candidates: List[Candidate] = field(default_factory=list)
    safe: bool = True

    def to_dict(self):
        return {
            "risk_location": self.risk_location,
            "intervention_location": self.intervention_location,
            "action": self.action,
            "duration_sec": self.duration_sec,
            "reason": self.reason,
            "confidence": round(self.confidence, 3),
            "risk_without_action": round(self.risk_without_action, 3),
            "risk_with_action": round(self.risk_with_action, 3),
            "candidates": [c.to_dict() for c in self.candidates],
            "safe": self.safe,
        }


def _upstream_nodes(sg: StadiumGraph, target: str, max_hops: int) -> List[str]:
    """BFS over predecessors, restricted to CHECKPOINT/GATE control-capable nodes."""
    visited = {target: 0}
    frontier = [target]
    result = []
    for _ in range(max_hops):
        nxt = []
        for u in frontier:
            for p in sg.predecessors(u):
                if p not in visited:
                    visited[p] = visited[u] + 1
                    nxt.append(p)
                    if sg.nodes[p].type in ("CHECKPOINT", "GATE"):
                        result.append(p)
        frontier = nxt
    return result


def _project_effect(sg: StadiumGraph, risk_node_id: str, intervention_node_id: str,
                     action: str, diversion_fraction: float = 0.35) -> Dict[str, float]:
    """
    Cheap forward projection: estimates the effect of diverting a fraction of
    the intervention node's outflow away from the path leading to risk_node,
    using the flow-conservation relation over the 60s horizon. This is
    intentionally lightweight so many candidates can be scored every cycle.
    """
    risk_node = sg.nodes[risk_node_id]
    horizon = 60
    CRUSH_DENSITY = 4.5  # same saturation reference used by the predictor
    critical_people = critical_people_for_node(risk_node)

    # Always use the same raw flow-conservation net rate for BOTH the baseline
    # and every candidate, so costs are directly comparable (apples-to-apples).
    if action == "DO_NOTHING":
        net = risk_node.inflow - risk_node.outflow
    else:
        # Diverting flow at the intervention node reduces the effective inflow
        # reaching the risk node proportionally to the diversion fraction.
        iv_node = sg.nodes[intervention_node_id]
        diverted_rate = iv_node.outflow * diversion_fraction
        net = (risk_node.inflow - diverted_rate * 0.6) - risk_node.outflow

    projected_people = max(0.0, risk_node.current_people + net * horizon)
    projected_density = min(CRUSH_DENSITY, projected_people / max(risk_node.area_m2, 1.0))

    # Time-to-critical is the key differentiator: reducing net accumulation
    # rate pushes the critical moment further out, even when the saturated
    # end-state density looks similar at a long horizon.
    remaining = max(0.0, critical_people - risk_node.current_people)
    ttc = float("inf") if net <= 0.05 else remaining / net

    # Global impact: count how many nodes would sit at/above CRITICAL band under this projection
    critical_nodes = 0
    max_density = projected_density
    for nid, n in sg.nodes.items():
        d = n.predicted_density.get(horizon, n.current_density)
        if nid == risk_node_id:
            d = projected_density
        elif action != "DO_NOTHING" and nid == intervention_node_id:
            d = max(0.0, d - diversion_fraction * 0.4)
        if d >= 3.2:  # ~CRITICAL band reference density
            critical_nodes += 1
        max_density = max(max_density, d)

    bottleneck_risk = 1.0 / (1.0 + ttc / 45.0) if ttc != float("inf") else 0.05
    travel_time_penalty = 0.0 if action == "DO_NOTHING" else 2.0 * diversion_fraction
    route_change_penalty = 0.0 if action == "DO_NOTHING" else 1.0
    congestion_duration = max(0.0, (projected_density - 3.2)) * 20.0

    return {
        "projected_density": projected_density,
        "max_density": max_density,
        "critical_nodes": critical_nodes,
        "bottleneck_risk": bottleneck_risk,
        "time_to_critical": ttc,
        "travel_time": travel_time_penalty,
        "route_change_penalty": route_change_penalty,
        "congestion_duration": congestion_duration,
    }


def _cost(effect: Dict[str, float]) -> float:
    w = config.INTERVENTION_WEIGHTS
    ttc = effect["time_to_critical"]
    ttc_benefit = min(ttc, 180.0) if ttc != float("inf") else 180.0
    return (w["max_density"] * effect["max_density"]
            + w["critical_nodes"] * effect["critical_nodes"]
            + w["bottleneck_risk"] * effect["bottleneck_risk"] * 14.0
            + w["travel_time"] * effect["travel_time"]
            + w["route_change"] * effect["route_change_penalty"]
            + w["congestion_duration"] * effect["congestion_duration"] / 10.0
            - 0.12 * ttc_benefit)


def plan_intervention(sg: StadiumGraph, risk_node_id: str,
                       destinations: Optional[List[str]] = None) -> Decision:
    """
    Main entry point: given a node predicted to become critical, search
    candidate (upstream checkpoint, action) pairs and pick the lowest-cost
    safe intervention. Always includes DO_NOTHING as the baseline.
    """
    destinations = destinations or [n for n in sg.nodes if sg.nodes[n].type == "EXIT"]
    risk_node = sg.nodes[risk_node_id]
    risk_without = node_risk(sg, risk_node,
                              risk_node.predicted_density.get(60, risk_node.current_density))["risk"]

    candidates: List[Candidate] = []

    # Baseline
    base_effect = _project_effect(sg, risk_node_id, risk_node_id, "DO_NOTHING")
    candidates.append(Candidate(
        checkpoint=risk_node_id, action="DO_NOTHING",
        projected_max_density=base_effect["max_density"],
        projected_critical_nodes=base_effect["critical_nodes"],
        projected_bottleneck_risk=base_effect["bottleneck_risk"],
        projected_travel_time=base_effect["travel_time"],
        congestion_duration=base_effect["congestion_duration"],
        cost=_cost(base_effect),
        reason="Baseline: no intervention applied.",
    ))

    upstream = _upstream_nodes(sg, risk_node_id, MAX_UPSTREAM_HOPS)
    action_diversion = {
        "HOLD": 0.5, "REDIRECT_LEFT": 0.4, "REDIRECT_RIGHT": 0.4,
        "SPLIT_FLOW": 0.3, "BLOCK_EDGE": 0.6, "CHANGE_DESTINATION_ROUTE": 0.45,
    }

    for cp in upstream:
        for action, frac in action_diversion.items():
            # Safety guardrail: never allow BLOCK_EDGE if it would disconnect a destination
            if action == "BLOCK_EDGE":
                would_disconnect = not all_destinations_reachable(sg, cp, destinations)
                if would_disconnect:
                    continue
            effect = _project_effect(sg, risk_node_id, cp, action, diversion_fraction=frac)
            cost = _cost(effect)
            cand = Candidate(
                checkpoint=cp, action=action,
                projected_max_density=effect["max_density"],
                projected_critical_nodes=effect["critical_nodes"],
                projected_bottleneck_risk=effect["bottleneck_risk"],
                projected_travel_time=effect["travel_time"],
                route_change_penalty=effect["route_change_penalty"],
                congestion_duration=effect["congestion_duration"],
                cost=cost,
                reason=(f"{cp} diverts ~{int(frac*100)}% of outbound flow away from the path "
                        f"leading to {risk_node_id}, reducing projected peak density."),
            )
            candidates.append(cand)

    candidates.sort(key=lambda c: c.cost)
    best = candidates[0]

    if best.action == "DO_NOTHING" or best.checkpoint == risk_node_id and best.action == "DO_NOTHING":
        # Only DO_NOTHING beat every real intervention -> nothing to apply
        risk_with = risk_without
        confidence = 0.5
        return Decision(
            risk_location=risk_node_id, intervention_location=None, action="DO_NOTHING",
            duration_sec=0, reason="No intervention reduces projected network risk further.",
            confidence=confidence, risk_without_action=risk_without, risk_with_action=risk_with,
            candidates=candidates[:6], safe=True,
        )

    # Confidence: normalised cost gap between best and baseline (bounded 0.5-0.97 for demo realism)
    baseline_cost = next(c.cost for c in candidates if c.action == "DO_NOTHING")
    gap = max(0.0, baseline_cost - best.cost)
    confidence = min(0.97, 0.5 + gap / (baseline_cost + 1e-6) * 0.6)

    baseline_bottleneck = next(c.projected_bottleneck_risk for c in candidates if c.action == "DO_NOTHING") or 1e-6
    relief_ratio = best.projected_bottleneck_risk / max(baseline_bottleneck, 1e-6)
    projected_risk_after = max(0.0, min(risk_without, risk_without * relief_ratio))

    duration = 15.0 if best.action == "HOLD" else 18.0

    reason = (f"{risk_node_id} predicted CRITICAL. {best.checkpoint} {best.action.replace('_',' ')} "
              f"reduces projected peak density from {round(risk_without,2)} to "
              f"{round(projected_risk_after,2)} network risk with no unsafe disconnection.")

    return Decision(
        risk_location=risk_node_id,
        intervention_location=best.checkpoint,
        action=best.action,
        duration_sec=duration,
        reason=reason,
        confidence=confidence,
        risk_without_action=risk_without,
        risk_with_action=projected_risk_after,
        candidates=candidates[:6],
        safe=True,
    )


def no_safe_action_decision(risk_node_id: str, risk_without: float) -> Decision:
    return Decision(
        risk_location=risk_node_id, intervention_location=None, action="NO_SAFE_ACTION",
        duration_sec=0,
        reason="NO SAFE AUTOMATIC ACTION - OPERATOR REVIEW REQUIRED.",
        confidence=0.0, risk_without_action=risk_without, risk_with_action=risk_without,
        candidates=[], safe=False,
    )
