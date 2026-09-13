"""
Phase 5 tests: Level Flow, Pre-Match Countdown, Live Win/Loss Evaluation & Scorecard Modal.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from app.simulation import SimulationEngine
from app.main import (
    app, player_sim, ai_sim, set_game_level, LevelConfigRequest, LEVEL_CONFIGS,
    start_sim, stop_sim, reset_sim
)

FRONTEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "frontend"))


def test_phase5_frontend_dom_elements():
    """Verify index.html contains all Phase 5 elements: Countdown, Level Selector pills, and Scorecard Modal."""
    with open(os.path.join(FRONTEND_DIR, "index.html"), encoding="utf-8") as f:
        html = f.read()

    # Pre-Match Countdown Overlay
    assert 'id="countdownOverlay"' in html
    assert 'id="countdownImg"' in html
    assert 'id="countdownFav"' in html
    assert 'id="countdownText"' in html
    assert 'id="countdownSub"' in html

    # Countdown graphic assets exist
    for img_name in ["1.png", "2.png", "3.png", "fav.png"]:
        assert os.path.isfile(os.path.join(FRONTEND_DIR, img_name)), f"Missing {img_name}"

    # Level Selector Navigation
    assert 'id="levelNav"' in html
    assert 'id="btnLvl1"' in html
    assert 'id="btnLvl2"' in html
    assert 'id="btnLvl3"' in html

    # Scorecard Modal
    assert 'id="scorecardModal"' in html
    assert 'id="scorecardCard"' in html
    assert 'id="scorecardBanner"' in html
    assert 'id="scorecardTrophy"' in html
    assert 'id="scorecardHeadline"' in html
    assert 'id="scorecardSubhead"' in html
    assert 'id="scPlayerEvac"' in html
    assert 'id="scPlayerBaseTime"' in html
    assert 'id="scPlayerHazards"' in html
    assert 'id="scPlayerFinalTime"' in html
    assert 'id="scPlayerStars"' in html
    assert 'id="scAiBadge"' in html
    assert 'id="scAiEvac"' in html
    assert 'id="scAiBaseTime"' in html
    assert 'id="scAiHazards"' in html
    assert 'id="scAiFinalTime"' in html
    assert 'id="scAiStars"' in html
    assert 'id="btnRetryLevel"' in html
    assert 'id="btnNextLevel"' in html


def test_phase5_frontend_styles():
    """Verify styles.css contains animations and responsive classes for Countdown and Scorecard."""
    with open(os.path.join(FRONTEND_DIR, "styles.css"), encoding="utf-8") as f:
        css = f.read()

    assert ".countdown-overlay" in css
    assert ".countdown-number" in css
    assert ".countdown-img" in css
    assert "@keyframes countdownScaleIn" in css
    assert "@keyframes countPulse" in css
    assert ".modal-backdrop" in css
    assert ".scorecard-card" in css
    assert "@keyframes modalPopIn" in css
    assert ".scorecard-banner" in css
    assert ".banner-defeat" in css
    assert ".scorecard-grid" in css
    assert ".modal-btn-primary" in css


def test_phase5_app_js_game_loop():
    """Verify app.js includes countdown sequence, level loading, win/loss evaluator, and star calculator."""
    with open(os.path.join(FRONTEND_DIR, "app.js"), encoding="utf-8") as f:
        js = f.read()

    assert "startPreMatchCountdown" in js
    assert "COUNTDOWN_IMAGE_SRCS" in js
    assert "loadLevel" in js
    assert "LEVEL_CONFIGS" in js
    assert "showScorecard" in js
    assert "calcStars" in js
    assert "formatMMSS" in js
    assert "playCountdownBeep" in js
    assert "playCountdownGo" in js
    assert "playWinSound" in js
    assert "playDefeatSound" in js


def test_backend_level_config_and_pause_for_countdown():
    """Setting level with start_immediately=False resets simulations and keeps them paused for countdown."""
    res = set_game_level(LevelConfigRequest(level=1, start_immediately=False))
    assert res["ok"] is True
    assert res["level"] == 1
    assert res["time_limit_sec"] == 150
    assert player_sim.running is False
    assert ai_sim.running is False
    assert ai_sim.difficulty == "novice"
    assert player_sim.evacuated_count == 0
    assert ai_sim.evacuated_count == 0

    # Test Level 2
    res2 = set_game_level(LevelConfigRequest(level=2, start_immediately=False))
    assert res2["ok"] is True
    assert res2["level"] == 2
    assert res2["time_limit_sec"] == 180
    assert ai_sim.difficulty == "pro"
    assert player_sim.running is False
    assert ai_sim.running is False

    # Test Level 3
    res3 = set_game_level(LevelConfigRequest(level=3, start_immediately=False))
    assert res3["ok"] is True
    assert res3["level"] == 3
    assert res3["time_limit_sec"] == 210
    assert ai_sim.difficulty == "super_predictive"
    assert player_sim.running is False
    assert ai_sim.running is False

    # Starting simulation after countdown activates both engines
    start_res = start_sim()
    assert start_res["running"] is True
    assert player_sim.running is True
    assert ai_sim.running is True

    # Clean up
    stop_sim()
    assert player_sim.running is False
    assert ai_sim.running is False


def test_level_config_targets_and_limits():
    """Verify LEVEL_CONFIGS mapping matches the arcade progression requirements."""
    assert LEVEL_CONFIGS[1]["difficulty"] == "novice"
    assert LEVEL_CONFIGS[1]["target_evacuation"] == 300
    assert LEVEL_CONFIGS[1]["time_limit_sec"] == 150
    assert LEVEL_CONFIGS[2]["difficulty"] == "pro"
    assert LEVEL_CONFIGS[2]["target_evacuation"] == 400
    assert LEVEL_CONFIGS[2]["time_limit_sec"] == 180
    assert LEVEL_CONFIGS[3]["difficulty"] == "super_predictive"
    assert LEVEL_CONFIGS[3]["target_evacuation"] == 600
    assert LEVEL_CONFIGS[3]["time_limit_sec"] == 210
