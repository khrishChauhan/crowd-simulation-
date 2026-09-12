import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import networkx as nx
from app.graph_model import build_stadium_graph
from app.routing import dynamic_astar, all_destinations_reachable


def test_graph_has_expected_node_count_range():
    sg = build_stadium_graph()
    assert 20 <= len(sg.nodes) <= 30


def test_graph_is_weakly_connected():
    sg = build_stadium_graph()
    assert nx.is_weakly_connected(sg.g)


def test_every_gate_can_reach_every_exit():
    sg = build_stadium_graph()
    gates = [n for n, d in sg.nodes.items() if d.type == "GATE"]
    exits = [n for n, d in sg.nodes.items() if d.type == "EXIT"]
    for g in gates:
        for e in exits:
            assert dynamic_astar(sg, g, e) is not None


def test_routing_returns_none_for_unknown_nodes():
    sg = build_stadium_graph()
    assert dynamic_astar(sg, "NOPE", "EXIT-01") is None


def test_blocking_an_edge_forces_alternate_route():
    sg = build_stadium_graph()
    path = dynamic_astar(sg, "CP-01", "EXIT-01")
    assert path is not None
    # block the first edge on the path and confirm a route still exists
    a, b = path[0], path[1]
    sg.edges[(a, b)].control_state = "BLOCK"
    sg.edges[(a, b)].enabled = False
    alt = dynamic_astar(sg, "CP-01", "EXIT-01")
    assert alt is not None


def test_all_destinations_reachable_guardrail():
    sg = build_stadium_graph()
    exits = [n for n, d in sg.nodes.items() if d.type == "EXIT"]
    assert all_destinations_reachable(sg, "GATE-01", exits)
