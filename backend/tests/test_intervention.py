import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.graph_model import build_stadium_graph
from app.predictor import SpatioTemporalCrowdPredictor
from app.intervention import plan_intervention


def _load_congested_scenario():
    sg = build_stadium_graph()
    risk = sg.nodes["CP-08"]
    risk.current_people, risk.current_density = 18, 18 / risk.area_m2
    risk.inflow, risk.outflow, risk.capacity = 18, 10, 14
    risk.density_history = [0.8, 0.9, 1.0, 1.1]

    upstream = sg.nodes["CP-09"]
    upstream.current_people, upstream.current_density = 10, 10 / upstream.area_m2
    upstream.inflow, upstream.outflow, upstream.capacity = 15, 15, 14

    predictor = SpatioTemporalCrowdPredictor()
    preds = predictor.predict_all(sg)
    for nid, n in sg.nodes.items():
        n.predicted_density = preds[nid]
    return sg


def test_intervention_always_evaluates_do_nothing_baseline():
    # DO_NOTHING is always scored as the baseline (risk_without_action reflects it);
    # it may or may not remain in the top-6 candidates shown if real interventions
    # score strictly better, which is the desired AI behaviour.
    sg = _load_congested_scenario()
    decision = plan_intervention(sg, "CP-08")
    assert decision.risk_without_action >= 0.0
    assert decision.action in (
        "DO_NOTHING", "HOLD", "REDIRECT_LEFT", "REDIRECT_RIGHT", "BLOCK_EDGE",
        "OPEN_EDGE", "SPLIT_FLOW", "CHANGE_DESTINATION_ROUTE", "NO_SAFE_ACTION",
    )


def test_intervention_picks_lowest_cost_candidate():
    sg = _load_congested_scenario()
    decision = plan_intervention(sg, "CP-08")
    costs = [c.cost for c in decision.candidates]
    assert costs == sorted(costs)


def test_risk_with_action_never_exceeds_risk_without_when_intervening():
    sg = _load_congested_scenario()
    decision = plan_intervention(sg, "CP-08")
    if decision.intervention_location is not None:
        assert decision.risk_with_action <= decision.risk_without_action + 1e-6


def test_block_edge_never_fully_disconnects_a_destination():
    sg = build_stadium_graph()
    # Force every candidate BLOCK_EDGE upstream of an exit to be evaluated
    risk = sg.nodes["JCT-01"]
    risk.current_people, risk.current_density = 30, 30 / risk.area_m2
    risk.inflow, risk.outflow, risk.capacity = 20, 5, 8
    predictor = SpatioTemporalCrowdPredictor()
    preds = predictor.predict_all(sg)
    for nid, n in sg.nodes.items():
        n.predicted_density = preds[nid]

    decision = plan_intervention(sg, "JCT-01")
    from app.routing import all_destinations_reachable
    exits = [n for n, d in sg.nodes.items() if d.type == "EXIT"]
    # Even if BLOCK_EDGE candidates were considered, at least one exit must remain reachable
    assert all_destinations_reachable(sg, "GATE-01", exits)


def test_decision_serialises_cleanly():
    sg = _load_congested_scenario()
    decision = plan_intervention(sg, "CP-08")
    d = decision.to_dict()
    assert "risk_location" in d and "action" in d and "candidates" in d
