import sys, os, math
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.simulation import SimulationEngine
from app import config


def test_simulation_steps_without_error():
    sim = SimulationEngine()
    for _ in range(50):
        sim.step()
    assert sim.tick_count == 50


def test_no_negative_or_nan_state():
    sim = SimulationEngine()
    for _ in range(120):
        sim.step()
    for n in sim.sg.nodes.values():
        assert n.current_people >= -1e-6
        assert not math.isnan(n.current_density)
        assert 0.0 <= n.risk <= 1.0


def test_agent_population_never_exceeds_cap():
    sim = SimulationEngine()
    sim.inject_crowd_surge("GATE-01", count=5000)
    assert len(sim.agents) <= config.MAX_PARTICLES


def test_all_demo_scenarios_run_without_crashing():
    sim = SimulationEngine()
    for _ in range(20):
        sim.step()
    sim.inject_crowd_surge("GATE-01", 50)
    for _ in range(20):
        sim.step()
    sim.close_exit("EXIT-03")
    for _ in range(20):
        sim.step()
    sim.trigger_counterflow()
    for _ in range(20):
        sim.step()
    sim.set_emergency(True)
    for _ in range(10):
        sim.step()
    sim.set_emergency(False)
    sim.toggle_camera_offline("CAM-01", True)
    obs = sim.cameras.observe_all(sim.sg)
    assert obs["CAM-01"].status == "OFFLINE"
    sim.toggle_camera_offline("CAM-01", False)


def test_reset_restores_baseline_population():
    # Pre-seeding disabled: simulation starts with 0 agents and resets to 0
    sim = SimulationEngine()
    assert len(sim.agents) == 0
    sim.inject_crowd_surge("GATE-01", 100)
    assert len(sim.agents) == 100
    sim.reset()
    assert len(sim.agents) == 0

    # Level 1 also starts with 0 agents
    sim1 = SimulationEngine(level=1)
    assert len(sim1.agents) == 0


def test_emergency_corridor_returns_a_path_when_feasible():
    sim = SimulationEngine()
    path = sim.emergency_corridor("GATE-01", "EXIT-01")
    assert path is not None and path[0] == "GATE-01" and path[-1] == "EXIT-01"


def test_snapshot_is_json_serialisable_shape():
    sim = SimulationEngine()
    sim.step()
    snap = sim.snapshot()
    for key in ("graph", "particles", "cameras", "metrics", "mode"):
        assert key in snap
    assert snap["mode"] == "DEMO_MODE"
    if snap["particles"]:
        p = snap["particles"][0]
        assert "is_congested" in p
        assert "is_slow" in p


def test_strict_binary_two_speed_system():
    sim = SimulationEngine()
    sim.step()
    # Pick a valid edge
    edge_key = next(iter(sim.sg.edges.keys()))
    edge = sim.sg.edges[edge_key]
    u, v = edge.source, edge.target
    rated_capacity = getattr(edge, "capacity", 0.0)
    if rated_capacity and rated_capacity > 0:
        threshold_x = min(config.NPC_DEFAULT_CONGESTION_THRESHOLD, config.NPC_CONGESTION_CAPACITY_RATIO * rated_capacity)
    else:
        threshold_x = config.NPC_DEFAULT_CONGESTION_THRESHOLD

    # Case 1: Below threshold X -> FAST (10.0), is_congested = False
    sim.edge_live_count[(u, v)] = max(0, int(threshold_x) - 1)
    agent = next(iter(sim.agents.values()))
    agent.current_node = u
    agent.target_node = v
    agent.progress = 0.0
    agent.is_congested = False
    dt = config.SIMULATION_DT
    expected_fast_delta = (config.NPC_SPEED_FAST * dt * 2.5) / max(edge.length, 1.0)

    sim._advance_on_edge(agent, dt)
    assert not agent.is_congested
    assert math.isclose(agent.progress, expected_fast_delta, rel_tol=1e-5)

    # Case 2: At or above threshold X -> SLOW (3.0), is_congested = True
    sim.edge_live_count[(u, v)] = max(int(threshold_x), 8) + 2
    agent.current_node = u
    agent.target_node = v
    agent.progress = 0.0
    agent.is_congested = False
    expected_slow_delta = (config.NPC_SPEED_SLOW * dt * 2.5) / max(edge.length, 1.0)

    sim._advance_on_edge(agent, dt)
    assert agent.is_congested
    assert math.isclose(agent.progress, expected_slow_delta, rel_tol=1e-5)


def test_spawn_boost_and_earlier_congestion_threshold():
    # Pre-seeding disabled: all levels start with 0 agents
    sim1 = SimulationEngine(level=1)
    assert len(sim1.agents) == 0
    sim2 = SimulationEngine(level=2)
    assert len(sim2.agents) == 0
    sim3 = SimulationEngine(level=3)
    assert len(sim3.agents) == 0

    # Step simulation for 1 second (20 ticks) and verify arcade spawn rate ~10 to 18 agents spawned
    for _ in range(20):
        sim1.step()
    assert 10 <= sim1.total_spawned <= 18

