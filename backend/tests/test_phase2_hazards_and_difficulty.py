"""
Phase 2 tests: 50% Hazard & Penalty Engine, 3 AI Difficulty Tiers, and Level Config API.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from app.simulation import SimulationEngine
from app.main import (
    app, player_sim, ai_sim, set_game_level, get_game_level,
    health, LevelConfigRequest, LEVEL_CONFIGS
)
from app import config


# ---------------------------------------------------------------------------
# Hazard Engine & Incident Penalties
# ---------------------------------------------------------------------------

def test_hazard_detection_and_penalty_addition():
    """When a node exceeds 50% capacity, probabilistic roll triggers HAZARD_INCIDENT and +3s penalty."""
    sim = SimulationEngine(seed=42)
    node = sim.sg.nodes["CP-01"]

    # Set people count to exceed 50% capacity (ratio >= 0.50)
    node.current_people = node.capacity * config.HAZARD_CAPACITY_MULTIPLIER * 0.8
    initial_penalty = sim.penalty_seconds
    initial_incidents = sim.incident_count

    # Run check multiple times to ensure probabilistic 50% roll triggers
    triggered = False
    for _ in range(20):
        sim._check_hazards()
        if sim.incident_count > initial_incidents:
            triggered = True
            break

    assert triggered, "Expected at least one hazard incident over 20 checks with ratio >= 0.50"
    assert sim.penalty_seconds >= initial_penalty + config.HAZARD_PENALTY_SECONDS
    assert sim.incident_count > initial_incidents

    # Check that HAZARD_INCIDENT event was logged with expected structure
    hazard_events = [e for e in sim.events if e["type"] == "HAZARD_INCIDENT"]
    assert len(hazard_events) > 0
    event = hazard_events[-1]
    assert event["payload"]["node"] in ("CP-01", "HUB-01")
    assert event["payload"]["penalty"] == 3.0
    assert "total_penalties" in event["payload"]


def test_hazard_metrics_in_snapshot():
    """snapshot()['metrics'] must contain penalty_seconds, incident_count, and difficulty."""
    sim = SimulationEngine(seed=42, arena_id="player", difficulty="pro")
    sim.penalty_seconds = 6.0
    sim.incident_count = 2

    snap = sim.snapshot()
    assert snap["arena_id"] == "player"
    assert snap["difficulty"] == "pro"
    assert snap["incident_count"] == 2

    metrics = snap["metrics"]
    assert "penalty_seconds" in metrics
    assert "incident_count" in metrics
    assert "difficulty" in metrics
    assert metrics["penalty_seconds"] == 6.0
    assert metrics["incident_count"] == 2
    assert metrics["difficulty"] == "pro"


# ---------------------------------------------------------------------------
# 3 AI Difficulty Tiers
# ---------------------------------------------------------------------------

def test_novice_ai_sluggish_latency_and_restricted_actions():
    """Novice AI only replans every 10 ticks and only chooses HOLD or DO_NOTHING."""
    sim = SimulationEngine(seed=42, arena_id="ai", difficulty="novice")
    node = sim.sg.nodes["CP-01"]
    node.risk = 0.95
    node.predicted_density[60] = 3.8

    # Tick 4: 4 - 0 = 4 < 10, so novice AI should skip intervention
    sim.tick_count = 4
    sim.last_intervention_tick = 0
    sim._evaluate_and_intervene()
    assert sim.active_decision is None or sim.last_intervention_tick == 0

    # At tick 10: 10 - 0 = 10 >= 10, novice AI evaluates
    sim.tick_count = 10
    sim._evaluate_and_intervene()
    assert sim.last_intervention_tick == 10
    assert sim.active_decision is not None
    # Action MUST be either DO_NOTHING or HOLD
    assert sim.active_decision.action in ("DO_NOTHING", "HOLD")

    # If we step 2 ticks (tick 12), novice AI should NOT replan (12 - 10 = 2 < 10)
    sim.tick_count = 12
    sim.active_decision = None
    sim._evaluate_and_intervene()
    assert sim.active_decision is None


def test_pro_ai_full_toolkit_and_split_flow():
    """Pro AI replans every 4 ticks and evaluates full toolkit including SPLIT_FLOW."""
    sim = SimulationEngine(seed=42, arena_id="ai", difficulty="pro")
    assert config.AI_ALLOWED_ACTIONS["pro"] == [
        "DO_NOTHING", "HOLD", "REDIRECT_LEFT", "REDIRECT_RIGHT", "SPLIT_FLOW"
    ]

    node = sim.sg.nodes["CP-01"]
    node.risk = 0.95
    node.predicted_density[60] = 3.8

    sim.tick_count = 4
    sim.last_intervention_tick = 0
    sim._evaluate_and_intervene()
    assert sim.last_intervention_tick == 4
    assert sim.active_decision is not None
    # Action must be in pro's allowed actions
    assert sim.active_decision.action in config.AI_ALLOWED_ACTIONS["pro"]


def test_super_predictive_ai_anticipatory_rerouting():
    """Super-predictive AI anticipates bottlenecks at +30s horizon and proactively intervenes."""
    sim = SimulationEngine(seed=42, arena_id="ai", difficulty="super_predictive")

    # Zero out all node populations so the metering step finds nothing at >=45% capacity
    # (the seeded population puts agents in transit, but nodes start empty enough with this reset)
    for node in sim.sg.nodes.values():
        node.current_people = 0
        node.current_density = 0.0

    # Set future predicted density at +30s to critical on a checkpoint with incoming flow
    pred_node = sim.sg.nodes["CP-08"]
    pred_node.current_people, pred_node.current_density = 18, 18 / pred_node.area_m2
    pred_node.inflow, pred_node.outflow, pred_node.capacity = 18, 10, 14
    pred_node.predicted_density[30] = 3.5

    upstream = sim.sg.nodes["CP-09"]
    upstream.current_people = 10
    upstream.inflow, upstream.outflow, upstream.capacity = 15, 15, 14

    sim.tick_count = 2
    sim.last_intervention_tick = 0
    sim._evaluate_and_intervene()

    # Must have triggered either a proactive anticipatory intervention or metering event
    valid_events = [
        e for e in sim.events
        if e["type"] in ("PROACTIVE_INTERVENTION", "ZERO_PENALTY_METERING", "PROACTIVE_METERING")
    ]
    assert len(valid_events) > 0
    assert sim.active_decision is not None


# ---------------------------------------------------------------------------
# Game Level Configuration API
# ---------------------------------------------------------------------------

def test_game_level_api_configuration():
    """POST /api/game/level configures AI difficulty, target evacuation, and resets match."""
    # Test Level 1 -> Novice
    data1 = set_game_level(LevelConfigRequest(level=1))
    assert data1["ok"] is True
    assert data1["level"] == 1
    assert data1["ai_difficulty"] == "novice"
    assert data1["target_evacuation"] == 300
    assert ai_sim.difficulty == "novice"
    assert player_sim.target_evacuation == 300

    # Test Level 2 -> Pro
    data2 = set_game_level(LevelConfigRequest(level=2))
    assert data2["level"] == 2
    assert data2["ai_difficulty"] == "pro"
    assert data2["target_evacuation"] == 400
    assert ai_sim.difficulty == "pro"

    # Test Level 3 -> Super-Predictive
    data3 = set_game_level(LevelConfigRequest(level=3))
    assert data3["level"] == 3
    assert data3["ai_difficulty"] == "super_predictive"
    assert data3["target_evacuation"] == 600
    assert ai_sim.difficulty == "super_predictive"

    # Test GET /api/game/level
    get_data = get_game_level()
    assert get_data["current_level"] == 3
    assert len(get_data["available_levels"]) == 3

    # Test /api/health includes level, incidents, and penalty seconds
    health_data = health()
    assert health_data["level"] == 3
    assert "incident_count" in health_data["player"]
    assert "penalty_seconds" in health_data["player"]
    assert "difficulty" in health_data["ai"]
    assert health_data["ai"]["difficulty"] == "super_predictive"
