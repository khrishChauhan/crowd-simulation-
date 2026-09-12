# CrowdShield AI

**Predict the crowd. Control the flow. Prevent the danger.**

CrowdShield AI is a predictive crowd-intelligence and autonomous flow-control
prototype for large stadiums. It is not a people-counter — it observes,
predicts 15/30/60s ahead, evaluates counterfactual interventions, and
continuously re-optimises a live 2D digital twin of a stadium, in a closed
control loop:

```
OBSERVE → ESTIMATE → PREDICT → SIMULATE → OPTIMIZE → CONTROL → OBSERVE...
```

> **This is a hackathon prototype.** It uses 5 pre-recorded/simulated CCTV
> feeds and a fully local agent-based crowd simulation as ground truth — see
> [Prototype limitations](#prototype-limitations--production-roadmap) below.
> Nothing here is a validated real-world safety system.

---

## 1. Project structure

```
crowdshield-ai/
├── backend/
│   ├── app/
│   │   ├── main.py            FastAPI app, REST + WebSocket endpoints
│   │   ├── config.py          All tunable constants (no magic numbers elsewhere)
│   │   ├── graph_model.py     Stadium graph (Node/Edge/StadiumGraph) + 2D layout
│   │   ├── routing.py         Dynamic risk-aware A* / Dijkstra
│   │   ├── risk_engine.py     Crowd Risk Severity Index (CRSI) + explainability
│   │   ├── predictor.py       SpatioTemporalCrowdPredictor (hybrid forecaster)
│   │   ├── detectors.py       Surge / counterflow / cascade detection
│   │   ├── intervention.py    Counterfactual intervention planner (core AI)
│   │   ├── flow_optimizer.py  Capacity-aware min-cost-flow route splitting
│   │   ├── cv_pipeline.py     5-camera manager, OpenCV proxy + simulation fallback
│   │   ├── simulation.py      Agent-based crowd sim + MPC-style control loop
│   │   └── db.py              SQLite schema, seed data, run logging
│   ├── tests/                 28 unit tests across graph/risk/prediction/intervention/simulation
│   └── requirements.txt
├── frontend/
│   ├── index.html             Mission-control dashboard shell
│   ├── styles.css             Dark/cyan technical design system
│   └── app.js                 WebSocket client + Canvas2D digital twin renderer
├── data/cameras/               Drop camera_01..05.mp4 here (optional)
├── docs/architecture.md        Mermaid diagrams, prototype vs production
├── .env.example
└── README.md
```

### A note on the frontend stack

The spec's preferred stack was Next.js/React. This environment has no
outbound network access for `npm install`, so **the frontend is instead
a dependency-free HTML/CSS/JS app** using the Canvas2D API directly for the
particle/graph rendering — it needs zero build step, opens instantly, and
degrades to nothing (no bundler drift, no missing `node_modules`). All the
"hard" work (prediction, risk, optimization, simulation) lives in the
Python backend exactly as specified. If you do want a React/Next.js shell
later, `app.js`'s render loop and WebSocket client translate directly into
a `useEffect` + `<canvas ref>` component.

---

## 2. Installation

```bash
# Backend
cd backend
python3 -m venv .venv && source .venv/bin/activate   # optional but recommended
pip install -r requirements.txt

# Frontend has no dependencies to install.
```

## 3. Running locally

```bash
# Terminal 1 — backend (FastAPI + WebSocket, runs the simulation continuously)
cd backend
uvicorn app.main:app --reload --port 8000

# Terminal 2 — frontend (any static file server works)
cd frontend
python3 -m http.server 5500
```

Open **http://localhost:5500** in your browser. The dashboard connects to
`http://localhost:8000` automatically (override with
`window.CROWDSHIELD_API_BASE` in `index.html` if you deploy the backend
elsewhere).

## 4. Adding real camera footage (optional)

Drop up to 5 `.mp4` files into `data/cameras/` (see
`data/cameras/README.md` for exact filenames). Any camera without a
matching file automatically runs in **SIMULATION FALLBACK** — the app
never crashes or blocks on missing footage.

## 5. Running the test suite

```bash
cd backend
pip install pytest   # already in requirements.txt
pytest tests/ -v
```
28 tests cover graph connectivity, dynamic A* routing, CRSI bounds,
bottleneck math, predictor stability, intervention cost-ordering and
safety guardrails, and full-scenario simulation robustness (no NaNs,
no negative counts, population cap respected, all 4 demo scenarios run
without error).

---

## 6. Demo instructions

1. Start backend + frontend as above.
2. Click **PRESENTATION MODE** (top right) to maximise the digital twin.
3. Use the **DEMO CONTROL BAR** at the bottom:
   - **▶ START DEMO** — ensures the simulation is running (it starts running
     automatically on backend boot).
   - **⚠ INJECT CROWD SURGE** — spawns a burst of new agents at GATE-01.
     Watch inflow climb on the nearby checkpoints; a `SURGE DETECTED` event
     appears in the log before density itself looks dangerous.
   - Within the next few control cycles (~2–10s), watch for
     `BOTTLENECK PREDICTED … TTC: Ns`, then `AI SIMULATED N INTERVENTIONS`,
     then `OPTIMAL ACTION SELECTED`. The **AI INSIGHT** panel updates with
     risk location, intervention point, action, and a plain-English reason.
     Particles visibly reroute; the risk score in the panel drops from
     "risk without action" to "risk with action".
   - **🚪 CLOSE EXIT-03** — simulates a gate failure. A `CROWD CASCADE RISK`
     chain is logged, and downstream traffic reroutes to the remaining exits.
   - **↔ COUNTERFLOW** — sends one group out through GATE-01 while a fresh
     group enters toward SEATING-C, creating genuine opposing traffic on a
     shared corridor; watch `COUNTERFLOW DETECTED` fire and the corridor
     highlight on the twin.
   - **⚖ DO-NOTHING VS AI** — opens a side-by-side comparison using the most
     recent real intervention decision's actual risk-without/risk-with
     numbers (never fabricated).
   - **🚨 EMERGENCY MODE** — every checkpoint switches to `EMERGENCY`
     control state and the twin shows a full-screen alert overlay.
   - **🔮 PREDICT FUTURE** — cycles the NOW / +15s / +30s / +60s horizon
     toggle, revealing ghost (semi-transparent) predicted particle positions
     ahead of the solid current ones.
4. Click any node on the twin to open its **CHECKPOINT DETAIL** panel:
   current density, flow, capacity, time-to-critical, and a bar-chart
   breakdown of *why* its risk score is what it is.
5. **RESET** at any time returns to the deterministic seeded baseline
   (`DEMO_RANDOM_SEED = 42`).

---

## 7. Implemented algorithms

| Area | Algorithm |
|---|---|
| Routing | Dynamic risk-aware Dijkstra/A* — `edge_cost = distance + λ1·congestion + λ2·predicted_risk + λ3·utilization + λ4·travel_time` |
| Flow | Conservation model `N(t+dt) ≈ N(t) + inflow·dt − outflow·dt`; NetworkX min-cost-flow for multi-route splitting, with a proportional-split fallback |
| Prediction | Hybrid: flow-conservation projection + `GradientBoostingRegressor` (bootstrapped on synthetic physically-generated trajectories) + graph-propagation neighbour blending, behind a stable `SpatioTemporalCrowdPredictor` interface a GNN could later replace |
| Risk | Crowd Risk Severity Index (CRSI) — explainable weighted blend of density, density growth, flow imbalance, downstream capacity, counterflow, and prediction risk, normalised to [0,1] with a 5-band prototype simplification |
| Bottleneck | `time_to_critical = (critical_people − current_people) / net_accumulation_rate`, `bottleneck_probability` via a logistic transform of inflow/outflow imbalance vs. headroom |
| Surge | Rolling-window inflow growth ratio at gates |
| Counterflow | Opposing-direction flow ratio per bidirectional edge pair |
| Cascade | Greedy downstream walk picking the least-headroom neighbour at each hop |
| Intervention | Counterfactual candidate search over `{DO_NOTHING, HOLD, REDIRECT_LEFT/RIGHT, BLOCK_EDGE, OPEN_EDGE, SPLIT_FLOW, CHANGE_DESTINATION_ROUTE}` × upstream checkpoints (BFS ≤3 hops), scored by a configurable weighted cost function and time-to-critical benefit, subject to a no-disconnection safety guardrail |
| Control | MPC-style receding-horizon loop, re-planning every ~2s, with automatic action expiry + reopen-if-safe |

## 8. AI decision pipeline (how a checkpoint decision is made)

1. Every control cycle, every `CHECKPOINT`/`JUNCTION` node's CRSI risk is
   computed from its current + predicted state.
2. The highest-risk node not already under an active control action is
   the **risk location**.
3. `intervention.plan_intervention()` searches nodes **upstream** of the
   risk location (not just the risk location itself) and evaluates every
   `(node, action)` pair by projecting its effect on the risk location's
   time-to-critical and network-wide peak density.
4. Every candidate's cost is compared against a `DO_NOTHING` baseline
   using the *same* projection formula, so costs are directly comparable.
5. The lowest-cost candidate is selected (unless nothing beats
   `DO_NOTHING`, or the only viable actions would disconnect a
   destination — then the system either does nothing or asks for
   operator review).
6. The chosen action is applied to the graph (`control_state`,
   `control_expires_tick`), which changes how agents route at that node
   on the very next tick — closing the OBSERVE → CONTROL → OBSERVE loop.

## 9. Simulation engine

An agent-based crowd model where **node/edge occupancy and flow are
derived directly from agent positions** — not a separate "fake metrics"
layer. Agents walk the stadium graph toward a destination (seating zone or
exit), slow down under local edge congestion, and probabilistically comply
with checkpoint redirect/hold instructions (`compliance_probability`,
`response_delay`). On arrival they dwell, then pick a new destination,
keeping the population organically circulating (ingress + egress +
natural counterflow) between demo-triggered surges. This is what makes the
feedback loop real: an applied `REDIRECT_RIGHT` measurably changes agent
paths within the next tick, which changes density/flow, which changes the
next risk computation, which changes the next AI decision.

## 10. Five-camera pipeline

`cv_pipeline.CameraManager` owns exactly 5 cameras (`CAM-01`..`CAM-05`),
each mapped to a checkpoint (`config.CAMERA_CHECKPOINT_MAP`, runtime
configurable via `POST /api/cameras/mapping`). For each camera:

- If a video file exists **and** OpenCV is available: background
  subtraction + contour counting produces a motion-proxy people estimate
  (status `CV_MOTION_PROXY`), blended with the simulation's ground truth
  for a stable demo signal. Swapping in real YOLO+ByteTrack detection is a
  drop-in change to `Camera._process_frame()`.
- Otherwise: **SIMULATION FALLBACK** — a synthetic but coherent
  observation derived from the linked node's live simulated state, clearly
  labelled in the UI (never silently pretending to be live).
- `toggle_camera_offline()` demonstrates graceful degradation: an offline
  camera reports `OFFLINE`/0 confidence, and the risk engine continues
  operating off graph/neighbour state for that checkpoint.

## 11. API reference

REST (`http://localhost:8000`):

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/stadium` | Full graph (nodes + edges) |
| GET | `/api/cameras` | Live camera observations |
| GET | `/api/checkpoints` | All checkpoint nodes |
| GET | `/api/checkpoints/{id}` | Detail + time-to-critical for one checkpoint |
| GET | `/api/crowd-state` | Full current snapshot (same shape as the WS push) |
| GET | `/api/predictions?horizon=30` | Predicted density per node |
| GET | `/api/predictions/ghosts?horizon=30` | Ghost particle positions |
| GET | `/api/risks` | CRSI + breakdown per node |
| GET | `/api/interventions` | Recent AI decisions |
| GET | `/api/analytics` | Metrics history + recent events |
| GET | `/api/events` | Recent event log |
| POST | `/api/simulation/{start,stop,reset}` | Simulation lifecycle |
| POST | `/api/scenario/start` | `{scenario, params}` — surge / gate_failure / counterflow / camera_failure |
| POST | `/api/control/action` | Manual operator override of a checkpoint |
| POST | `/api/emergency` | `{active, responder_start, responder_destination}` |
| POST | `/api/shadow-mode` | Toggle AI-recommends-without-applying mode |
| POST | `/api/cameras/mapping` | Reconfigure camera → checkpoint mapping |

WebSocket: `ws://localhost:8000/ws` streams `crowd_state` snapshots
(~2/sec) and `event` messages as they occur.

---

## Prototype limitations & production roadmap

See [`docs/architecture.md`](docs/architecture.md) for the full
prototype-vs-production table and safety-guardrail list. In short: this
demo's "ground truth" crowd is itself simulated (there is no real stadium
to observe), the CV pipeline is a lightweight motion proxy rather than a
trained YOLO+ByteTrack model, and every risk band/weight in `config.py` is
a tunable prototype constant, not a validated safety threshold. It is
built to demonstrate the *architecture and decision pipeline* of a future
live deployment — not to be deployed as-is at a real venue.
