import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import config
from app.graph_model import build_stadium_graph
from app.predictor import SpatioTemporalCrowdPredictor


def test_predictor_returns_all_horizons():
    sg = build_stadium_graph()
    predictor = SpatioTemporalCrowdPredictor()
    node = sg.nodes["CP-01"]
    node.current_people, node.current_density = 10, 0.6
    node.inflow, node.outflow = 8, 5
    preds = predictor.predict_node(sg, node)
    assert set(preds.keys()) == set(config.PREDICTION_HORIZONS)


def test_predictor_output_bounded_and_finite():
    sg = build_stadium_graph()
    predictor = SpatioTemporalCrowdPredictor()
    node = sg.nodes["CP-01"]
    node.current_people, node.current_density = 999, 40.0
    node.inflow, node.outflow = 999, 0
    preds = predictor.predict_node(sg, node)
    for v in preds.values():
        assert v == v  # not NaN
        assert 0.0 <= v <= 5.0


def test_predict_all_covers_every_node():
    sg = build_stadium_graph()
    predictor = SpatioTemporalCrowdPredictor()
    preds = predictor.predict_all(sg)
    assert set(preds.keys()) == set(sg.nodes.keys())


def test_higher_inflow_yields_higher_or_equal_prediction():
    sg = build_stadium_graph()
    predictor = SpatioTemporalCrowdPredictor()
    node_low = sg.nodes["CP-01"]
    node_low.current_people, node_low.current_density = 10, 0.6
    node_low.inflow, node_low.outflow = 2, 5

    node_high = sg.nodes["CP-02"]
    node_high.current_people, node_high.current_density = 10, 0.6
    node_high.inflow, node_high.outflow = 15, 5

    p_low = predictor.predict_node(sg, node_low)[60]
    p_high = predictor.predict_node(sg, node_high)[60]
    assert p_high >= p_low
