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
    sim = SimulationEngine()
    sim.inject_crowd_surge("GATE-01", 100)
    assert len(sim.agents) > 220
    sim.reset()
    assert 200 <= len(sim.agents) <= 240


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
