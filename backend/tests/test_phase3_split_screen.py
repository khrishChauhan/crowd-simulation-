"""
Phase 3 tests: Frontend Split-Screen Arena, Race HUD, and Dual-Arena Endpoints.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from app.main import (
    app, player_sim, ai_sim, get_dual_crowd_state,
    start_sim, stop_sim, reset_sim, set_game_level, LevelConfigRequest
)

FRONTEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "frontend"))


def test_frontend_files_exist():
    """Verify index.html, styles.css, and app.js exist in frontend/."""
    assert os.path.isfile(os.path.join(FRONTEND_DIR, "index.html"))
    assert os.path.isfile(os.path.join(FRONTEND_DIR, "styles.css"))
    assert os.path.isfile(os.path.join(FRONTEND_DIR, "app.js"))


def test_index_html_dual_arena_elements():
    """index.html must contain dual canvases, race HUD, tug-of-war, and control IDs."""
    with open(os.path.join(FRONTEND_DIR, "index.html"), encoding="utf-8") as f:
        html = f.read()

    required_ids = [
        "playerCanvas", "aiCanvas",
        "playerEvacText", "playerPctText", "playerProgressBar",
        "aiEvacText", "aiPctText", "aiProgressBar", "aiDiffBadge",
        "matchTimer", "penaltyDisplay", "tugHuman", "tugAi", "raceLeadStatus",
        "panelPlayer", "panelAi", "playerFlash", "aiFlash",
        "aiDecisionPill", "aiDecisionText",
        "btnLvl1", "btnLvl2", "btnLvl3",
        "btnStart", "btnPause", "btnReset",
        "btnSurge", "btnCloseExit", "btnCounterflow", "btnEmergency",
        "btnSoundToggle", "chipWs",
    ]
    for element_id in required_ids:
        assert f'id="{element_id}"' in html, f"Missing id={element_id} in index.html"


def test_styles_css_classes():
    """styles.css must include split-screen grid, tug-of-war track, and hazard pulse animation."""
    with open(os.path.join(FRONTEND_DIR, "styles.css"), encoding="utf-8") as f:
        css = f.read()

    assert ".dual-arena-grid" in css
    assert ".race-hud" in css
    assert ".tug-track" in css
    assert ".hazard-perimeter-flash.hazard-active" in css
    assert "@keyframes flashPulse" in css


def test_app_js_features():
    """app.js must implement dual canvas rendering, 4-stage lifecycle, and hazard popouts."""
    with open(os.path.join(FRONTEND_DIR, "app.js"), encoding="utf-8") as f:
        js = f.read()

    # Dual canvas contexts
    assert "playerCanvas" in js and "aiCanvas" in js
    assert "drawArena" in js

    # 4-stage lifecycle particles
    assert "INGRESS" in js
    assert "DWELL" in js
    assert "EGRESS" in js

    # Floating hazard popout stamps
    assert "activePopouts" in js
    assert "CRUSH ALERT" in js or "CRUSH RISK" in js

    # Controls & networking
    assert "apiPost" in js
    assert "apiGet" in js
    assert "connectWS" in js


def test_dual_state_endpoint():
    """get_dual_crowd_state must return both player and ai states."""
    dual = get_dual_crowd_state()
    assert "player" in dual and "ai" in dual
    assert dual["player"]["arena_id"] == "player"
    assert dual["ai"]["arena_id"] == "ai"
    assert "metrics" in dual["player"]
    assert "particles" in dual["player"]
    assert "graph" in dual["player"]


def test_synchronized_match_controls():
    """start_sim, stop_sim, reset_sim must control both player_sim and ai_sim."""
    start_sim()
    assert player_sim.running is True
    assert ai_sim.running is True

    stop_sim()
    assert player_sim.running is False
    assert ai_sim.running is False

    reset_sim()
    assert player_sim.tick_count == 0
    assert ai_sim.tick_count == 0


@pytest.mark.anyio
async def test_admin_background_upload_and_level2():
    """Verify level2.png exists, upload endpoint functions, and Level 2 map loads."""
    from app.main import upload_admin_background, save_admin_map_by_level, get_admin_map_by_level, AdminMapPayload
    from fastapi import UploadFile
    import io

    # 1. level2.png must exist in frontend
    lvl2_path = os.path.join(FRONTEND_DIR, "level2.png")
    assert os.path.isfile(lvl2_path), "frontend/level2.png does not exist"

    # 2. Upload endpoint test
    fake_file = UploadFile(filename="test_upload.png", file=io.BytesIO(b"\x89PNG\r\n\x1a\ntestdata"))
    res = await upload_admin_background(level=2, file=fake_file)
    assert res["ok"] is True
    assert res["path"] == "custom_bg_level_2.png"

    uploaded_path = os.path.join(FRONTEND_DIR, "custom_bg_level_2.png")
    assert os.path.isfile(uploaded_path)
    # Clean up created upload file
    try:
        os.remove(uploaded_path)
    except OSError:
        pass

    # 3. Verify Level 2 map file loads
    lvl2_map = await get_admin_map_by_level(2)
    assert "nodes" in lvl2_map
    assert "edges" in lvl2_map
    assert len(lvl2_map["nodes"]) > 0

