"""
Phase 4 tests: Pure Map-Click Tactile Controls, 4-State Corridor Cycle, and Safety Reachability.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from app.simulation import SimulationEngine, Agent
from app.graph_model import build_stadium_graph
from app.routing import dynamic_astar, edge_cost
from app.main import (
    app, player_sim, ai_sim, manual_control_action, control_corridor,
    ControlActionRequest, CorridorControlRequest, _is_exit_reachable_from_seating
)

FRONTEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "frontend"))


def test_phase4_frontend_dom_elements():
    """Verify index.html contains tactileLegend and safetyToast for zero-toolbar design."""
    with open(os.path.join(FRONTEND_DIR, "index.html")) as f:
        html = f.read()

    assert 'id="tactileLegend"' in html
    assert 'id="safetyToast"' in html
    assert 'id="playerCanvas"' in html
    assert 'id="aiCanvas"' in html


def test_phase4_styles_classes():
    """Verify styles.css includes tactile-legend and safety-toast shake styles."""
    with open(os.path.join(FRONTEND_DIR, "styles.css")) as f:
        css = f.read()

    assert ".tactile-legend" in css
    assert ".safety-toast" in css
    assert "@keyframes toastShake" in css


def test_gate_toggle_and_reopen():
    """Toggling a gate/checkpoint to BLOCK and back to NORMAL updates node control_state."""
    res_block = manual_control_action(ControlActionRequest(
        arena="player",
        checkpoint_id="CP-01",
        action="BLOCK",
        duration_sec=30.0
    ))
    assert res_block["ok"] is True
    assert player_sim.sg.nodes["CP-01"].control_state == "BLOCK"

    # Reopen
    res_open = manual_control_action(ControlActionRequest(
        arena="player",
        checkpoint_id="CP-01",
        action="NORMAL",
        duration_sec=30.0
    ))
    assert res_open["ok"] is True
    assert player_sim.sg.nodes["CP-01"].control_state == "NORMAL"


def test_safety_reachability_guardrail_node():
    """Attempting to block all exits must be rejected with CANNOT DISCONNECT ALL EXITS."""
    # Temporarily block 2 exits
    for ex in ["EXIT-01", "EXIT-02"]:
        res = manual_control_action(ControlActionRequest(
            arena="player",
            checkpoint_id=ex,
            action="BLOCK"
        ))
        assert res["ok"] is True

    # Attempting to block the 3rd exit must be rejected
    res_last = manual_control_action(ControlActionRequest(
        arena="player",
        checkpoint_id="EXIT-03",
        action="BLOCK"
    ))
    assert res_last["ok"] is False
    assert res_last["error"] == "CANNOT DISCONNECT ALL EXITS"
    assert player_sim.sg.nodes["EXIT-03"].control_state != "BLOCK"

    # Reopen all exits
    for ex in ["EXIT-01", "EXIT-02", "EXIT-03"]:
        manual_control_action(ControlActionRequest(
            arena="player",
            checkpoint_id=ex,
            action="NORMAL"
        ))


def test_corridor_4_state_cycle():
    """Corridor control cycling through FORWARD_ONLY, REVERSE_ONLY, BLOCK, and BIDIRECTIONAL."""
    src, tgt = "GATE-01", "CP-08"

    # State 2: FORWARD_ONLY
    res_fwd = control_corridor(CorridorControlRequest(
        arena="player",
        source=src,
        target=tgt,
        state="FORWARD_ONLY"
    ))
    assert res_fwd["ok"] is True
    assert player_sim.sg.edges[(src, tgt)].enabled is True
    assert player_sim.sg.edges[(src, tgt)].control_state == "NORMAL"
    assert player_sim.sg.edges[(tgt, src)].enabled is False
    assert player_sim.sg.edges[(tgt, src)].control_state == "BLOCK"

    # State 3: REVERSE_ONLY
    res_rev = control_corridor(CorridorControlRequest(
        arena="player",
        source=src,
        target=tgt,
        state="REVERSE_ONLY"
    ))
    assert res_rev["ok"] is True
    assert player_sim.sg.edges[(src, tgt)].enabled is False
    assert player_sim.sg.edges[(src, tgt)].control_state == "BLOCK"
    assert player_sim.sg.edges[(tgt, src)].enabled is True
    assert player_sim.sg.edges[(tgt, src)].control_state == "NORMAL"

    # State 4: BLOCK
    res_blk = control_corridor(CorridorControlRequest(
        arena="player",
        source=src,
        target=tgt,
        state="BLOCK"
    ))
    assert res_blk["ok"] is True
    assert player_sim.sg.edges[(src, tgt)].enabled is False
    assert player_sim.sg.edges[(src, tgt)].control_state == "BLOCK"
    assert player_sim.sg.edges[(tgt, src)].enabled is False
    assert player_sim.sg.edges[(tgt, src)].control_state == "BLOCK"

    # State 1: BIDIRECTIONAL
    res_bi = control_corridor(CorridorControlRequest(
        arena="player",
        source=src,
        target=tgt,
        state="BIDIRECTIONAL"
    ))
    assert res_bi["ok"] is True
    assert player_sim.sg.edges[(src, tgt)].enabled is True
    assert player_sim.sg.edges[(src, tgt)].control_state == "NORMAL"
    assert player_sim.sg.edges[(tgt, src)].enabled is True
    assert player_sim.sg.edges[(tgt, src)].control_state == "NORMAL"


def test_routing_avoids_blocked_node_and_corridor():
    """dynamic_astar must never route through a blocked corridor or blocked node."""
    sg = build_stadium_graph()

    # Base path exists
    path_init = dynamic_astar(sg, "GATE-01", "SEATING-A")
    assert path_init is not None

    # Block edge
    first_step = path_init[1]
    sg.edges[("GATE-01", first_step)].control_state = "BLOCK"
    sg.edges[("GATE-01", first_step)].enabled = False

    path_rerouted = dynamic_astar(sg, "GATE-01", "SEATING-A")
    assert path_rerouted is not None
    assert path_rerouted[1] != first_step, "Path should reroute around blocked corridor"

    # Block destination node
    sg.nodes["SEATING-A"].control_state = "BLOCK"
    cost = edge_cost(sg, path_rerouted[-2], "SEATING-A")
    assert cost == float("inf"), "Cost to enter blocked node must be infinite"


def test_agent_transit_turn_around_on_blocked_edge():
    """An agent traversing an edge that becomes blocked turns around if progress < 0.6."""
    sim = SimulationEngine(seed=42, arena_id="player")
    edge = sim.sg.edges[("GATE-01", "CP-08")]

    agent = Agent(
        id="test_agent",
        current_node="GATE-01",
        target_node="CP-08",
        destination="SEATING-A",
        route=["CP-08", "SEATING-A"],
        progress=0.3,
        x=0.0,
        y=0.0
    )
    sim.agents[agent.id] = agent

    # Block the edge
    edge.control_state = "BLOCK"
    edge.enabled = False

    sim._advance_on_edge(agent, dt=0.5)

    # Agent should have reversed direction towards GATE-01
    assert agent.target_node == "GATE-01"
    assert agent.current_node == "CP-08"
    assert agent.progress > 0.0
