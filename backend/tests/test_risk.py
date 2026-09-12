import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.graph_model import build_stadium_graph
from app.risk_engine import band_for, node_risk, bottleneck_probability, time_to_critical_seconds, critical_people_for_node


def test_band_boundaries():
    assert band_for(0.0) == "VERY_LOW"
    assert band_for(0.19) == "VERY_LOW"
    assert band_for(0.25) == "LOW"
    assert band_for(0.45) == "MEDIUM"
    assert band_for(0.65) == "HIGH"
    assert band_for(0.95) == "CRITICAL"
    assert band_for(1.0) == "CRITICAL"


def test_risk_is_bounded_0_1():
    sg = build_stadium_graph()
    node = sg.nodes["CP-01"]
    node.current_people = 500
    node.current_density = 500 / node.area_m2
    node.inflow, node.outflow = 30, 2
    r = node_risk(sg, node, predicted_density_60=4.0)
    assert 0.0 <= r["risk"] <= 1.0
    assert all(0.0 <= v <= 1.0 for v in r["breakdown"].values())


def test_empty_node_has_low_risk():
    sg = build_stadium_graph()
    node = sg.nodes["CP-01"]
    r = node_risk(sg, node, predicted_density_60=0.0)
    assert r["risk"] < 0.3


def test_bottleneck_probability_bounded():
    sg = build_stadium_graph()
    node = sg.nodes["CP-01"]
    node.inflow, node.outflow, node.capacity, node.current_people = 20, 5, 10, 5
    p = bottleneck_probability(node)
    assert 0.0 <= p <= 1.0


def test_time_to_critical_infinite_when_not_accumulating():
    sg = build_stadium_graph()
    node = sg.nodes["CP-01"]
    node.inflow, node.outflow = 5, 10
    ttc = time_to_critical_seconds(node, critical_people_for_node(node))
    assert ttc == float("inf")


def test_time_to_critical_positive_when_accumulating():
    sg = build_stadium_graph()
    node = sg.nodes["CP-01"]
    node.current_people = 5
    node.inflow, node.outflow = 15, 5
    ttc = time_to_critical_seconds(node, critical_people_for_node(node))
    assert ttc > 0
