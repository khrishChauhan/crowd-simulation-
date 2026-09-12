"""
CrowdShield AI - Risk Engine
=============================
Computes the Crowd Risk Severity Index (CRSI): a continuous 0-1 score per
node, built from an explainable, weighted blend of features. The UI only
ever shows the 5-band simplification (VERY_LOW..CRITICAL); the underlying
continuous score plus a labelled breakdown is always available so the
dashboard can explain *why* a checkpoint is risky.

These weights/bands are PROTOTYPE constants (see config.py), not validated
safety thresholds.
"""
from __future__ import annotations

from typing import Dict

from . import config
from .graph_model import Node, StadiumGraph


def band_for(score: float) -> str:
    for lo, hi, label in config.RISK_BANDS:
        if lo <= score < hi:
            return label
    return "CRITICAL"


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


def node_risk(sg: StadiumGraph, node: Node, predicted_density_60: float = None) -> Dict[str, float]:
    """Returns dict with 'risk' (0-1) and per-factor 'breakdown' (each 0-1)."""
    cap_density = max(node.current_people, 0.01) / max(node.area_m2, 1.0)
    density_factor = _clip01(node.current_density / 4.0)  # 4 people/m^2 ~ crush density reference

    hist = node.density_history[-6:] if node.density_history else [node.current_density]
    growth = (node.current_density - hist[0]) / max(hist[0], 0.05) if hist else 0.0
    growth_factor = _clip01(max(0.0, growth))

    imbalance = abs(node.inflow - node.outflow) / max(node.capacity, 0.1)
    flow_imbalance_factor = _clip01(imbalance)

    downstream_caps = [sg.nodes[t].capacity - sg.nodes[t].current_people
                        for t in sg.neighbors(node.id) if t in sg.nodes]
    if downstream_caps:
        min_headroom = min(downstream_caps)
        downstream_factor = _clip01(1.0 - (min_headroom / max(node.capacity, 1.0)))
    else:
        downstream_factor = 0.2

    counterflow = 0.0
    in_edges = sg.in_edges(node.id) + sg.out_edges(node.id)
    if in_edges:
        counterflow = max((e.counterflow_ratio for e in in_edges), default=0.0)
    counterflow_factor = _clip01(counterflow / max(config.COUNTERFLOW_RATIO_ALERT * 2, 0.01))

    pred = predicted_density_60 if predicted_density_60 is not None else node.current_density
    prediction_factor = _clip01(pred / 4.0)

    w = config.RISK_WEIGHTS
    total_w = sum(w.values())
    breakdown = {
        "density": density_factor,
        "density_growth": growth_factor,
        "flow_imbalance": flow_imbalance_factor,
        "downstream_capacity": downstream_factor,
        "counterflow": counterflow_factor,
        "prediction_risk": prediction_factor,
    }
    score = sum(w[k] * v for k, v in breakdown.items()) / total_w
    return {"risk": _clip01(score), "breakdown": breakdown}


def bottleneck_probability(node: Node) -> float:
    """Simple logistic-style transform of inflow/outflow imbalance vs capacity headroom."""
    imbalance = node.inflow - node.outflow
    headroom = max(node.capacity - node.current_people, 0.01)
    raw = imbalance / headroom
    # squashing function, no external deps
    prob = 1.0 / (1.0 + pow(2.71828, -3.0 * raw))
    return _clip01(prob)


def time_to_critical_seconds(node: Node, critical_people: float) -> float:
    """
    Estimates seconds until `node.current_people` reaches the CRITICAL density
    threshold, given the current net accumulation rate. Returns +inf if the
    node is not accumulating (net rate <= 0).
    """
    net_rate = node.inflow - node.outflow
    if net_rate <= 0.05:
        return float("inf")
    remaining = max(critical_people - node.current_people, 0.0)
    return remaining / net_rate


def critical_people_for_node(node: Node) -> float:
    """People count corresponding to the CRITICAL risk density band (0.80)."""
    crush_reference = 4.0  # people/m^2
    return config.CRITICAL_RISK_THRESHOLD * crush_reference * node.area_m2
