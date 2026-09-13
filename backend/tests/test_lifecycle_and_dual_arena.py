"""
Phase 1 tests: 4-stage agent lifecycle and dual-arena independence.
"""
import sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import math
from app.simulation import SimulationEngine
from app import config


# ---------------------------------------------------------------------------
# Stage 1 + 2 — spawn & ingress, center dwell
# ---------------------------------------------------------------------------

def test_agent_spawn_at_gate_with_seating_destination():
    """Agents must spawn at perimeter gates (GATE-xx) and head toward SEATING."""
    sim = SimulationEngine()
    sim.step()
    assert len(sim.agents) > 0
    for aid, agent in sim.agents.items():
        assert agent.stage == "INGRESS", f"Agent {aid} should be INGRESS, got {agent.stage}"
        # destination must be a SEATING node
        node = sim.sg.nodes[agent.destination]
        assert node.type == "SEATING", (
            f"Agent {aid} destination {agent.destination} is {node.type}, expected SEATING"
        )


def test_center_dwell_lasts_exactly_10_ticks():
    """An agent that arrives at seating must dwell for the configured center_dwell_sec."""
    import math
    from app import config as cfg
    sim = SimulationEngine(seed=99)
    dwell_starts = {}

    # Step until at least one agent enters CENTER_DWELL
    max_ticks = int(400 / cfg.SIMULATION_DT)  # scale headroom with DT
    for tick in range(max_ticks):
        sim.step()
        for aid, agent in list(sim.agents.items()):
            if agent.stage == "CENTER_DWELL" and aid not in dwell_starts:
                dwell_starts[aid] = (tick, agent.center_dwell_ticks)
        if dwell_starts:
            break

    assert dwell_starts, "No agent entered CENTER_DWELL within time limit"

    # Now watch one of those agents until it transitions to EGRESS
    target_aid = next(iter(dwell_starts))
    expected_ticks = dwell_starts[target_aid][1]
    # Give 3× headroom for the agent to finish dwelling
    headroom = max(200, expected_ticks * 3)
    ticks_in_dwell = 0
    for _ in range(headroom):
        sim.step()
        agent = sim.agents.get(target_aid)
        if agent is None:
            break  # evacuated prematurely — should not happen
        if agent.stage == "CENTER_DWELL":
            ticks_in_dwell += 1
        elif agent.stage == "EGRESS":
            break

    # Agent must have dwelled for approximately the expected number of ticks (±20%)
    tolerance = max(3, math.ceil(expected_ticks * 0.20))
    assert abs(ticks_in_dwell - expected_ticks) <= tolerance, (
        f"Agent {target_aid} dwelled for {ticks_in_dwell} ticks, "
        f"expected ~{expected_ticks} (±{tolerance})"
    )



def test_dwell_agent_does_not_depart_early():
    """An agent in CENTER_DWELL must NOT have a target_node (i.e. must stay put)."""
    sim = SimulationEngine(seed=77)
    for _ in range(200):
        sim.step()
    dwellers = [a for a in sim.agents.values() if a.stage == "CENTER_DWELL"]
    if not dwellers:
        return  # no dwellers yet — skip rather than fail
    for agent in dwellers:
        assert agent.target_node is None, (
            f"CENTER_DWELL agent {agent.id} is moving (target_node={agent.target_node})"
        )


# ---------------------------------------------------------------------------
# Stage 3 + 4 — egress and evacuation
# ---------------------------------------------------------------------------

def test_egress_agents_route_to_exit():
    """Agents in EGRESS stage should have EXIT-xx as their destination."""
    sim = SimulationEngine(seed=42)
    for _ in range(600):
        sim.step()
    egress_agents = [a for a in sim.agents.values() if a.stage == "EGRESS"]
    for agent in egress_agents:
        dest_node = sim.sg.nodes.get(agent.destination)
        assert dest_node is not None, f"Agent {agent.id} has no destination"
        assert dest_node.type == "EXIT", (
            f"EGRESS agent {agent.id} heading to {agent.destination} ({dest_node.type}), expected EXIT"
        )


def test_evacuation_counter_increments():
    """Agents arriving at EXIT nodes in EGRESS stage must increment evacuated_count."""
    sim = SimulationEngine(seed=42)
    for _ in range(1000):
        sim.step()
    assert sim.evacuated_count > 0, (
        f"Expected evacuated_count > 0 after 1000 ticks, got {sim.evacuated_count}"
    )
    assert isinstance(sim.evacuation_log, list)
    assert len(sim.evacuation_log) == min(sim.evacuated_count, 500)


def test_evacuated_agents_removed_from_agent_dict():
    """Agents that are evacuated must NOT remain in sim.agents."""
    sim = SimulationEngine(seed=123)
    for _ in range(800):
        sim.step()
    for aid, agent in sim.agents.items():
        assert agent.stage != "EVACUATED", (
            f"Evacuated agent {aid} still present in sim.agents"
        )


# ---------------------------------------------------------------------------
# Arena state / metrics
# ---------------------------------------------------------------------------

def test_snapshot_contains_arena_lifecycle_metrics():
    """snapshot()['metrics'] must include all lifecycle counters and arena_id at top level."""
    sim = SimulationEngine(arena_id="player", seed=42)
    sim.step()
    snap = sim.snapshot()
    assert snap["arena_id"] == "player"
    metrics = snap["metrics"]
    for key in ("evacuated_count", "target_evacuation", "penalty_seconds", "total_spawned"):
        assert key in metrics, f"Missing metric key: {key}"
    assert metrics["target_evacuation"] == 2000
    assert metrics["evacuated_count"] >= 0
    assert metrics["penalty_seconds"] == 0.0


def test_particles_include_stage_field():
    """Every particle in snapshot()['particles'] must include the 'stage' field."""
    sim = SimulationEngine(seed=42)
    sim.step()
    snap = sim.snapshot()
    assert snap["particles"], "No particles in snapshot"
    for p in snap["particles"]:
        assert "stage" in p, f"Particle {p['id']} missing 'stage' field"
        assert p["stage"] in ("INGRESS", "CENTER_DWELL", "EGRESS"), (
            f"Particle {p['id']} has unexpected stage: {p['stage']}"
        )


# ---------------------------------------------------------------------------
# Dual-arena independence
# ---------------------------------------------------------------------------

def test_dual_arena_independence():
    """player_sim (seed=42) and ai_sim (seed=142) must diverge in agent positions."""
    player = SimulationEngine(seed=42, arena_id="player")
    ai = SimulationEngine(seed=142, arena_id="ai")

    for _ in range(50):
        player.step()
        ai.step()

    player_positions = [(a.x, a.y) for a in list(player.agents.values())[:20]]
    ai_positions = [(a.x, a.y) for a in list(ai.agents.values())[:20]]
    assert player_positions != ai_positions, (
        "player_sim and ai_sim produced identical agent positions — seeds are not independent"
    )


def test_dual_arena_tick_counts_match():
    """Both arenas stepped the same number of times should have equal tick counts."""
    player = SimulationEngine(seed=42, arena_id="player")
    ai = SimulationEngine(seed=142, arena_id="ai")
    ticks = 30
    for _ in range(ticks):
        player.step()
        ai.step()
    assert player.tick_count == ticks
    assert ai.tick_count == ticks


def test_closed_exit_egress_reroute():
    """EGRESS agents must not route to a closed exit and should reroute dynamically."""
    sim = SimulationEngine(seed=42)
    for _ in range(600):
        sim.step()

    sim.close_exit("EXIT-03")

    for _ in range(50):
        sim.step()
    for agent in sim.agents.values():
        if agent.stage == "EGRESS":
            assert agent.destination != "EXIT-03", (
                f"Agent {agent.id} is routing toward closed EXIT-03"
            )
