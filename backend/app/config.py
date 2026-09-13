"""
CrowdShield AI - Central Configuration
========================================
All tunable constants live here so nothing is hard-coded throughout the
codebase. These are PROTOTYPE values, tuned for a compelling, stable
hackathon demo - not scientifically validated crowd-safety thresholds.
"""

# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------
SIMULATION_DT = 0.05             # seconds per physics tick (20 ticks/s for smooth movement)
CONTROL_INTERVAL_TICKS = 40      # run the AI control loop every N ticks (~2s wall-clock)
MAX_PARTICLES = 1400             # hard cap on simulated agents
DEMO_RANDOM_SEED = 42            # deterministic hackathon demo

# ---------------------------------------------------------------------------
# Prediction horizons (seconds)
# ---------------------------------------------------------------------------
PREDICTION_HORIZONS = [15, 30, 60, 120]

# ---------------------------------------------------------------------------
# Risk bands (Crowd Risk Severity Index, CRSI) - PROTOTYPE risk bands only.
# ---------------------------------------------------------------------------
RISK_BANDS = [
    (0.00, 0.20, "VERY_LOW"),
    (0.20, 0.40, "LOW"),
    (0.40, 0.60, "MEDIUM"),
    (0.60, 0.80, "HIGH"),
    (0.80, 1.01, "CRITICAL"),
]

CRITICAL_RISK_THRESHOLD = 0.80

# Weighted contributors to CRSI. Sum need not be 1 - result is renormalised.
RISK_WEIGHTS = {
    "density": 0.22,
    "density_growth": 0.14,
    "flow_imbalance": 0.16,
    "downstream_capacity": 0.14,
    "counterflow": 0.10,
    "prediction_risk": 0.24,
}

# ---------------------------------------------------------------------------
# Graph / routing weights - dynamic A* edge cost blend
# ---------------------------------------------------------------------------
GRAPH_WEIGHTS = {
    "distance": 1.0,
    "congestion": 0.5,     # lambda1 - reduced for direct shortest-path commitment
    "predicted_risk": 0.5, # lambda2 - reduced for direct shortest-path commitment
    "utilization": 0.5,    # lambda3
    "travel_time": 0.5,    # lambda4
}

# ---------------------------------------------------------------------------
# Intervention objective function weights (see optimization.intervention)
# ---------------------------------------------------------------------------
INTERVENTION_WEIGHTS = {
    "max_density": 3.0,      # alpha
    "critical_nodes": 2.5,   # beta
    "bottleneck_risk": 2.0,  # gamma
    "travel_time": 0.6,      # delta
    "route_change": 0.8,     # epsilon (penalises unnecessary switching)
    "congestion_duration": 1.0,  # zeta
}

DEFAULT_CAPACITY = {
    "GATE": 6.0,        # people/sec discharge capacity
    "CHECKPOINT": 5.0,
    "JUNCTION": 8.0,
    "SEATING": 3.0,
    "EXIT": 7.0,
    "EMERGENCY": 4.0,
}

NODE_AREA_M2 = {
    "GATE": 20.0,
    "CHECKPOINT": 16.0,
    "JUNCTION": 24.0,
    "SEATING": 60.0,
    "EXIT": 18.0,
    "EMERGENCY": 14.0,
}

# ---------------------------------------------------------------------------
# Cameras
# ---------------------------------------------------------------------------
CAMERA_IDS = ["CAM-01", "CAM-02", "CAM-03", "CAM-04", "CAM-05"]

# Default camera -> checkpoint mapping (user-configurable at runtime)
CAMERA_CHECKPOINT_MAP = {
    "CAM-01": "CP-01",
    "CAM-02": "CP-03",
    "CAM-03": "CP-05",
    "CAM-04": "CP-07",
    "CAM-05": "CP-09",
}

CAMERA_DATA_DIR = "data/cameras"
CAMERA_FILENAMES = {
    "CAM-01": "camera_01.mp4",
    "CAM-02": "camera_02.mp4",
    "CAM-03": "camera_03.mp4",
    "CAM-04": "camera_04.mp4",
    "CAM-05": "camera_05.mp4",
}

# ---------------------------------------------------------------------------
# Surge / counterflow detection
# ---------------------------------------------------------------------------
SURGE_INFLOW_GROWTH_RATIO = 1.35   # inflow must grow by >35% over window
SURGE_WINDOW_SAMPLES = 4
COUNTERFLOW_RATIO_ALERT = 0.18

# ---------------------------------------------------------------------------
# Hazard Engine & Incident Penalties
# ---------------------------------------------------------------------------
HAZARD_CAPACITY_MULTIPLIER = 2.5   # capacity * 2.5 gives hazard ceiling
HAZARD_OCCUPANCY_THRESHOLD = 0.50  # >= 50% crowded triggers check
HAZARD_TRIGGER_PROBABILITY = 0.50  # 50% random roll
HAZARD_PENALTY_SECONDS = 3.0       # +3s penalty per incident

# ---------------------------------------------------------------------------
# AI Competitor Difficulty Tiers
# ---------------------------------------------------------------------------
DIFFICULTY_TIERS = ("novice", "pro", "super_predictive")
AI_REPLAN_INTERVAL_TICKS = {
    "novice": 10,             # ~5.0s sluggish latency
    "pro": 4,                 # ~2.0s fast latency
    "super_predictive": 2,    # ~1.0s instant/proactive
}
AI_ALLOWED_ACTIONS = {
    "novice": ["DO_NOTHING", "HOLD"],
    "pro": ["DO_NOTHING", "HOLD", "REDIRECT_LEFT", "REDIRECT_RIGHT", "SPLIT_FLOW"],
    "super_predictive": [
        "DO_NOTHING", "HOLD", "REDIRECT_LEFT", "REDIRECT_RIGHT",
        "SPLIT_FLOW", "BLOCK_EDGE", "CHANGE_DESTINATION_ROUTE"
    ],
}

# ---------------------------------------------------------------------------
# Strict Binary 2-Speed Simulation System
# ---------------------------------------------------------------------------
NPC_SPEED_FAST = 15.0                     # Base brisk speed (15.0 units/sec)
NPC_SPEED_SLOW = 4.5                      # Congested speed (4.5 units/sec)
NPC_CONGESTION_CAPACITY_RATIO = 0.45      # 45% of edge capacity
NPC_DEFAULT_CONGESTION_THRESHOLD = 8.0    # 8 concurrent agents on a corridor segment

# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------
DB_PATH = "backend/crowdshield.db"
WEBSOCKET_BROADCAST_HZ = 2.0   # state pushes per second to frontend

