"""
CrowdShield AI - Simulation Engine
=====================================
Runs independently of the (simulated) video pipeline, per the architecture:

    CCTV --> observed state
    Simulation --> future crowd behaviour

Two coupled layers:

  1. Agent layer (visual + ground truth): a lightweight agent-based model.
     Every person-agent moves along the stadium graph's edges toward a
     destination, slowing down under local congestion (a simplified
     social-force-inspired rule), and reacting to checkpoint control
     actions with a per-agent compliance probability and response delay.
     Node/edge occupancy and flow are DERIVED DIRECTLY from agent
     positions, so the feedback loop required by the spec is real:
     control action -> agents respond -> density/flow changes -> risk
     changes -> next AI decision changes accordingly.

  2. MPC-style control loop, run every CONTROL_INTERVAL_TICKS ticks:
     OBSERVE -> ESTIMATE -> PREDICT -> SIMULATE -> OPTIMIZE -> CONTROL -> repeat.
"""
from __future__ import annotations

import itertools
import math
import random
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import config
from .graph_model import StadiumGraph, build_stadium_graph
from .predictor import SpatioTemporalCrowdPredictor
from .risk_engine import node_risk, band_for, bottleneck_probability, time_to_critical_seconds, critical_people_for_node
from .routing import dynamic_astar
from .intervention import plan_intervention, no_safe_action_decision, Decision
from .detectors import detect_surge, detect_counterflow, predict_cascade
from .cv_pipeline import CameraManager

DESTINATION_TYPES = ("SEATING", "EXIT")


@dataclass
class Agent:
    id: int
    x: float
    y: float
    current_node: str
    target_node: Optional[str] = None
    progress: float = 0.0
    route: List[str] = field(default_factory=list)
    destination: str = ""
    group_id: int = 0
    response_delay: int = 0
    compliance_probability: float = 0.85
    dwell_ticks: int = 0
    lateral_offset: float = 0.0
    ghost: bool = False


class SimulationEngine:
    def __init__(self, on_event=None, on_state=None):
        self.sg: StadiumGraph = build_stadium_graph()
        self.predictor = SpatioTemporalCrowdPredictor()
        self.cameras = CameraManager()
        self.rng = random.Random(config.DEMO_RANDOM_SEED)
        self.agents: Dict[int, Agent] = {}
        self._next_agent_id = itertools.count(1)
        self.tick_count = 0
        self.running = False
        self.shadow_mode = False
        self.presentation_mode = False
        self.on_event = on_event or (lambda e: None)
        self.on_state = on_state or (lambda s: None)
        self.events: List[dict] = []
        self.decisions_log: List[dict] = []
        self.active_decision: Optional[Decision] = None
        self.emergency_active = False
        self.arrival_counters: Dict[str, int] = {}
        self.departure_counters: Dict[str, int] = {}
        self.edge_transit_counters: Dict[tuple, int] = {}
        self._destinations = [n for n, node in self.sg.nodes.items() if node.type in DESTINATION_TYPES]
        self._seed_population(base_size=220)
        self.metrics_history: List[dict] = []

    # ---------------------------------------------------------------- setup
    def _seed_population(self, base_size: int):
        seating = [n for n, nd in self.sg.nodes.items() if nd.type == "SEATING"]
        for _ in range(base_size):
            start = self.rng.choice(seating)
            self._spawn_agent(start)

    def _spawn_agent(self, at_node: str, destination: Optional[str] = None):
        if len(self.agents) >= config.MAX_PARTICLES:
            return None
        dest = destination or self.rng.choice([d for d in self._destinations if d != at_node])
        route = dynamic_astar(self.sg, at_node, dest) or [at_node]
        node = self.sg.nodes[at_node]
        aid = next(self._next_agent_id)
        agent = Agent(
            id=aid, x=node.x, y=node.y, current_node=at_node, route=route[1:], destination=dest,
            group_id=self.rng.randint(0, 5),
            response_delay=self.rng.randint(1, 6),
            compliance_probability=self.rng.uniform(0.65, 0.97),
            lateral_offset=self.rng.uniform(-3.5, 3.5),
        )
        self.agents[aid] = agent
        return agent

    # -------------------------------------------------------------- ticking
    def step(self):
        dt = config.SIMULATION_DT
        self.tick_count += 1
        self._move_agents(dt)
        self._recompute_occupancy()

        if self.tick_count % config.CONTROL_INTERVAL_TICKS == 0:
            self._control_cycle()

        state = self.snapshot()
        self.metrics_history.append(state["metrics"])
        if len(self.metrics_history) > 240:
            self.metrics_history.pop(0)
        self.on_state(state)
        return state

    # ------------------------------------------------------------ movement
    def _move_agents(self, dt: float):
        for agent in list(self.agents.values()):
            if agent.target_node is None:
                self._depart_from_node(agent)
                continue
            self._advance_on_edge(agent, dt)

    def _depart_from_node(self, agent: Agent):
        node = self.sg.nodes[agent.current_node]

        # Checkpoint HOLD: agents wait, do not depart while dwell_ticks remain
        if node.control_state == "HOLD" and agent.dwell_ticks <= 0:
            agent.dwell_ticks = 3
        if agent.dwell_ticks > 0:
            agent.dwell_ticks -= 1
            return

        if not agent.route:
            # Arrived at destination: dwell, then pick a new one (keeps population alive)
            if agent.current_node == agent.destination:
                new_dest = self.rng.choice([d for d in self._destinations if d != agent.current_node])
                new_route = dynamic_astar(self.sg, agent.current_node, new_dest)
                if new_route:
                    agent.route = new_route[1:]
                    agent.destination = new_dest
            else:
                new_route = dynamic_astar(self.sg, agent.current_node, agent.destination)
                agent.route = new_route[1:] if new_route else []
            if not agent.route:
                return

        next_node_id = agent.route[0]
        edge = self.sg.edges.get((agent.current_node, next_node_id))

        # Redirect control at this node: with `compliance_probability`, agents obey
        cs = node.control_state
        if cs in ("REDIRECT_LEFT", "REDIRECT_RIGHT") and self.rng.random() < agent.compliance_probability:
            alt = self._alternate_neighbor(agent.current_node, next_node_id, cs)
            if alt:
                rerouted = dynamic_astar(self.sg, alt, agent.destination)
                if rerouted:
                    agent.route = [alt] + rerouted[1:]
                    next_node_id = alt
                    edge = self.sg.edges.get((agent.current_node, next_node_id))

        if edge is None or not edge.enabled or edge.control_state == "BLOCK":
            rerouted = dynamic_astar(self.sg, agent.current_node, agent.destination)
            if rerouted and len(rerouted) > 1:
                agent.route = rerouted[1:]
                next_node_id = agent.route[0]
                edge = self.sg.edges.get((agent.current_node, next_node_id))
            else:
                return  # no feasible route right now; wait this tick

        if edge is None:
            return

        agent.target_node = next_node_id
        agent.progress = 0.0
        agent.route = agent.route[1:]
        key = (agent.current_node, next_node_id)
        self.edge_transit_counters[key] = self.edge_transit_counters.get(key, 0) + 1
        self.departure_counters[agent.current_node] = self.departure_counters.get(agent.current_node, 0) + 1

    def _alternate_neighbor(self, node_id: str, avoid: str, direction_hint: str) -> Optional[str]:
        neighbors = [n for n in self.sg.neighbors(node_id) if n != avoid]
        if not neighbors:
            return None
        # crude left/right split by index parity - deterministic but non-trivial
        neighbors.sort()
        if direction_hint == "REDIRECT_LEFT":
            return neighbors[0]
        return neighbors[-1]

    def _advance_on_edge(self, agent: Agent, dt: float):
        edge = self.sg.edges.get((agent.current_node, agent.target_node))
        if edge is None:
            agent.current_node, agent.target_node = agent.target_node, None
            return
        occupancy = self.edge_live_count.get((agent.current_node, agent.target_node), 1) if hasattr(self, "edge_live_count") else 1
        max_concurrent = max(1.0, edge.capacity * 2.2)
        congestion = min(1.5, occupancy / max_concurrent)
        speed_factor = max(0.12, 1.0 - 0.65 * congestion)
        base_speed = 1.35  # m/s
        agent.progress += (base_speed * speed_factor * dt) / max(edge.length, 1.0)

        src = self.sg.nodes[agent.current_node]
        dst = self.sg.nodes[agent.target_node]
        t = min(1.0, agent.progress)
        agent.x = src.x + (dst.x - src.x) * t
        agent.y = src.y + (dst.y - src.y) * t

        if agent.progress >= 1.0:
            self.arrival_counters[agent.target_node] = self.arrival_counters.get(agent.target_node, 0) + 1
            agent.current_node = agent.target_node
            agent.target_node = None
            agent.progress = 0.0

    # ------------------------------------------------------------ occupancy
    def _recompute_occupancy(self):
        node_counts: Dict[str, float] = {nid: 0.0 for nid in self.sg.nodes}
        edge_counts: Dict[tuple, int] = {}
        for agent in self.agents.values():
            if agent.target_node is None:
                node_counts[agent.current_node] += 1.0
            else:
                key = (agent.current_node, agent.target_node)
                edge_counts[key] = edge_counts.get(key, 0) + 1
                # split occupancy credit between source/target based on progress
                node_counts[agent.current_node] += max(0.0, 1.0 - agent.progress)
                node_counts[agent.target_node] += agent.progress

        self.edge_live_count = edge_counts
        for nid, node in self.sg.nodes.items():
            node.current_people = node_counts.get(nid, 0.0)
            node.current_density = node.current_people / max(node.area_m2, 1.0)
            node.density_history.append(node.current_density)
            if len(node.density_history) > 40:
                node.density_history.pop(0)

        for key, edge in self.sg.edges.items():
            live = edge_counts.get(key, 0)
            edge.utilization = min(1.5, live / max(1.0, edge.capacity * 1.5))

    # -------------------------------------------------------------- control
    def _control_cycle(self):
        window_s = config.CONTROL_INTERVAL_TICKS * config.SIMULATION_DT

        # 1. ESTIMATE inflow/outflow from accumulated counters
        for nid, node in self.sg.nodes.items():
            arrivals = self.arrival_counters.get(nid, 0)
            departures = self.departure_counters.get(nid, 0)
            node.inflow = arrivals / window_s
            node.outflow = departures / window_s
            node.inflow_history.append(node.inflow)
            if len(node.inflow_history) > 20:
                node.inflow_history.pop(0)
        self.arrival_counters.clear()
        self.departure_counters.clear()

        for key, edge in self.sg.edges.items():
            transits = self.edge_transit_counters.get(key, 0)
            edge.current_flow = transits / window_s
        self.edge_transit_counters.clear()

        # 2. PREDICT
        preds = self.predictor.predict_all(self.sg)
        for nid, node in self.sg.nodes.items():
            node.predicted_density = preds[nid]

        # 3. Preventive detectors (surge / counterflow / cascade)
        for nid, node in self.sg.nodes.items():
            if node.type == "GATE":
                surge = detect_surge(nid, node.inflow_history)
                if surge:
                    self._log_event("SURGE_DETECTED", surge["message"], {"node": nid, **surge})

        counterflow_alerts = detect_counterflow(self.sg)
        for alert in counterflow_alerts:
            self._log_event("COUNTERFLOW_DETECTED", alert["message"], alert)

        # 4. RISK
        for nid, node in self.sg.nodes.items():
            result = node_risk(self.sg, node, node.predicted_density.get(60))
            node.risk = result["risk"]
            node.risk_breakdown = result["breakdown"]
        for key, edge in self.sg.edges.items():
            src_risk = self.sg.nodes[edge.source].risk
            edge.risk = min(1.0, 0.5 * src_risk + 0.5 * edge.utilization)

        # 5. Identify risk location(s) and OPTIMIZE + CONTROL
        self._auto_reopen()
        self._evaluate_and_intervene()

    def _log_event(self, event_type: str, message: str, payload: Optional[dict] = None):
        entry = {
            "timestamp": time.strftime("%H:%M:%S"),
            "type": event_type,
            "message": message,
            "payload": payload or {},
            "tick": self.tick_count,
        }
        self.events.append(entry)
        if len(self.events) > 300:
            self.events.pop(0)
        self.on_event(entry)

    def _auto_reopen(self):
        for nid, node in self.sg.nodes.items():
            if node.control_state != "NORMAL" and node.control_expires_tick is not None:
                if self.tick_count >= node.control_expires_tick:
                    result = node_risk(self.sg, node, node.predicted_density.get(60))
                    if result["risk"] < 0.5:
                        node.control_state = "NORMAL"
                        node.control_reason = None
                        node.control_expires_tick = None
                        self._log_event("ROUTE_RESTORED", f"{nid} route restored - risk normalised.",
                                         {"node": nid})
                    else:
                        node.control_expires_tick = self.tick_count + int(10 / config.SIMULATION_DT)
                        self._log_event("ACTION_EXTENDED", f"{nid} intervention extended - risk still elevated.",
                                         {"node": nid})

    def _evaluate_and_intervene(self):
        candidates = [(nid, n) for nid, n in self.sg.nodes.items()
                      if n.type in ("CHECKPOINT", "JUNCTION") and n.control_state == "NORMAL"]
        if not candidates:
            return
        risky = sorted(candidates, key=lambda kv: kv[1].risk, reverse=True)
        risk_node_id, risk_node = risky[0]

        predicted_60 = risk_node.predicted_density.get(60, risk_node.current_density)
        critical_people = critical_people_for_node(risk_node)
        ttc = time_to_critical_seconds(risk_node, critical_people)

        if risk_node.risk >= 0.6 or predicted_60 >= 3.2:
            if ttc != float("inf") and ttc < 90:
                self._log_event("BOTTLENECK_PREDICTED",
                                 f"BOTTLENECK PREDICTED at {risk_node_id}. TTC: {int(ttc)} sec",
                                 {"node": risk_node_id, "ttc": ttc})

            decision = plan_intervention(self.sg, risk_node_id)
            self.decisions_log.append(decision.to_dict())
            if len(self.decisions_log) > 50:
                self.decisions_log.pop(0)

            if not decision.safe:
                self._log_event("OPERATOR_REVIEW_REQUIRED", decision.reason, {"node": risk_node_id})
                return

            self._log_event("AI_SIMULATED_INTERVENTIONS",
                             f"AI SIMULATED {len(decision.candidates)} INTERVENTIONS", {})

            if decision.action == "DO_NOTHING" or decision.intervention_location is None:
                self.active_decision = decision
                return

            self._log_event("OPTIMAL_ACTION_SELECTED",
                             f"OPTIMAL ACTION SELECTED: {decision.intervention_location} "
                             f"{decision.action} ({int(decision.duration_sec)}s)",
                             decision.to_dict())

            if not self.shadow_mode:
                self._apply_action(decision)
            else:
                self._log_event("SHADOW_MODE_RECOMMENDATION",
                                 f"AI WOULD HAVE: {decision.action} at {decision.intervention_location}",
                                 decision.to_dict())
            self.active_decision = decision

    def _apply_action(self, decision: Decision):
        node = self.sg.nodes[decision.intervention_location]
        node.control_state = decision.action
        node.control_reason = decision.reason
        node.control_expires_tick = self.tick_count + int(decision.duration_sec / config.SIMULATION_DT)

        if decision.action == "BLOCK_EDGE":
            path = dynamic_astar(self.sg, decision.intervention_location, decision.risk_location)
            if path and len(path) > 1:
                edge = self.sg.edges.get((path[0], path[1]))
                if edge:
                    edge.control_state = "BLOCK"
                    edge.enabled = False

        self._log_event("CHECKPOINT_ACTION",
                         f"{decision.intervention_location} {decision.action} for "
                         f"{int(decision.duration_sec)}s", {"decision": decision.to_dict()})

    # -------------------------------------------------------------- scenarios / demo controls
    def inject_crowd_surge(self, gate_id: str = "GATE-01", count: int = 60):
        for _ in range(count):
            self._spawn_agent(gate_id, destination=self.rng.choice(
                [d for d in self._destinations if d.startswith("SEATING")]))
        self._log_event("SCENARIO_TRIGGER", f"Crowd surge injected at {gate_id} (+{count} people)",
                         {"gate": gate_id, "count": count})

    def close_exit(self, exit_id: str = "EXIT-03"):
        for pred in self.sg.predecessors(exit_id):
            edge = self.sg.edges.get((pred, exit_id))
            if edge:
                edge.enabled = False
                edge.control_state = "BLOCK"
        self._log_event("SCENARIO_TRIGGER", f"{exit_id} CLOSED - recalculating downstream routes",
                         {"exit": exit_id})
        for nid in self.sg.predecessors(exit_id):
            chain = predict_cascade(self.sg, nid)
            if chain:
                self._log_event("CROWD_CASCADE_RISK", f"Cascade risk chain: {' -> '.join(chain)}",
                                 {"chain": chain})

    def reopen_exit(self, exit_id: str = "EXIT-03"):
        for pred in self.sg.predecessors(exit_id):
            edge = self.sg.edges.get((pred, exit_id))
            if edge:
                edge.enabled = True
                edge.control_state = "NORMAL"
        self._log_event("SCENARIO_TRIGGER", f"{exit_id} reopened", {"exit": exit_id})

    def trigger_counterflow(self):
        seating_c_agents = [a for a in self.agents.values() if a.current_node == "SEATING-C"][:40]
        for agent in seating_c_agents:
            agent.destination = "GATE-01"
            route = dynamic_astar(self.sg, agent.current_node, "GATE-01")
            if route:
                agent.route = route[1:]
        for _ in range(40):
            self._spawn_agent("GATE-01", destination="SEATING-C")
        self._log_event("SCENARIO_TRIGGER", "Counterflow event triggered: two groups on a collision corridor",
                         {})

    def toggle_camera_offline(self, camera_id: str, offline: bool = True):
        self.cameras.set_offline(camera_id, offline)
        self._log_event("CAMERA_STATUS", f"{camera_id} {'OFFLINE' if offline else 'ONLINE'}",
                         {"camera_id": camera_id})

    def set_emergency(self, active: bool):
        self.emergency_active = active
        if active:
            for nid, node in self.sg.nodes.items():
                if node.type == "CHECKPOINT":
                    node.control_state = "EMERGENCY"
                    node.control_reason = "EMERGENCY MODE ACTIVE"
                    node.control_expires_tick = None
            self._log_event("EMERGENCY_MODE", "EMERGENCY MODE ACTIVATED - evacuation routing engaged", {})
        else:
            for nid, node in self.sg.nodes.items():
                if node.control_state == "EMERGENCY":
                    node.control_state = "NORMAL"
                    node.control_reason = None
            self._log_event("EMERGENCY_MODE", "Emergency mode deactivated", {})

    def emergency_corridor(self, start: str, destination: str):
        path = dynamic_astar(self.sg, start, destination)
        if path:
            self._log_event("EMERGENCY_CORRIDOR_ACTIVE",
                             f"EMERGENCY CORRIDOR ACTIVE: {' -> '.join(path)}", {"path": path})
        return path

    def reset(self):
        self.__init__(on_event=self.on_event, on_state=self.on_state)
        self._log_event("SYSTEM", "Simulation reset", {})

    # -------------------------------------------------------------- snapshot
    def total_crowd(self) -> float:
        return float(len(self.agents))

    def network_risk(self) -> float:
        risks = [n.risk for n in self.sg.nodes.values()]
        return sum(risks) / len(risks) if risks else 0.0

    def snapshot(self) -> dict:
        camera_obs = {k: v.to_dict() for k, v in self.cameras.observe_all(self.sg).items()}
        graph_dict = self.sg.to_dict()
        particles = [
            {"id": a.id, "x": round(a.x, 1), "y": round(a.y, 1), "group_id": a.group_id,
             "ghost": a.ghost, "destination": a.destination, "route": a.route}
            for a in self.agents.values()
        ]
        critical_nodes = [n.id for n in self.sg.nodes.values() if band_for(n.risk) == "CRITICAL"]
        predicted_bottlenecks = [n.id for n in self.sg.nodes.values()
                                  if n.predicted_density.get(60, 0) >= 3.2]
        active_interventions = [n.id for n in self.sg.nodes.values() if n.control_state != "NORMAL"]

        metrics = {
            "total_crowd": self.total_crowd(),
            "average_density": round(sum(n.current_density for n in self.sg.nodes.values())
                                      / max(1, len(self.sg.nodes)), 3),
            "peak_density": round(max((n.current_density for n in self.sg.nodes.values()), default=0), 3),
            "network_risk": round(self.network_risk(), 3),
            "critical_checkpoints": len(critical_nodes),
            "predicted_bottlenecks": len(predicted_bottlenecks),
            "active_interventions": len(active_interventions),
            "evacuation_readiness": round(max(0.0, 1.0 - self.network_risk()), 3),
        }

        return {
            "type": "crowd_state",
            "tick": self.tick_count,
            "timestamp": time.time(),
            "graph": graph_dict,
            "particles": particles,
            "cameras": camera_obs,
            "metrics": metrics,
            "active_decision": self.active_decision.to_dict() if self.active_decision else None,
            "mode": "DEMO_MODE",
            "shadow_mode": self.shadow_mode,
            "emergency_active": self.emergency_active,
        }

    def predicted_snapshot(self, horizon: int) -> List[dict]:
        """Ghost particle projection: rough forward-projects each agent along its route."""
        ghosts = []
        steps = max(1, int(horizon / config.SIMULATION_DT))
        for agent in self.agents.values():
            gx, gy = agent.x, agent.y
            route = list(agent.route)
            cur = agent.current_node
            remaining_progress = 1.0 - agent.progress if agent.target_node else 0.0
            budget = horizon
            if agent.target_node:
                edge = self.sg.edges.get((agent.current_node, agent.target_node))
                if edge:
                    remain_time = remaining_progress * edge.length / 1.3
                    if budget >= remain_time:
                        budget -= remain_time
                        cur = agent.target_node
                        gx, gy = self.sg.nodes[cur].x, self.sg.nodes[cur].y
                    else:
                        frac = budget / max(remain_time, 0.01)
                        gx = agent.x + (self.sg.nodes[agent.target_node].x - agent.x) * frac
                        gy = agent.y + (self.sg.nodes[agent.target_node].y - agent.y) * frac
                        budget = 0
            while budget > 0 and route:
                nxt = route.pop(0)
                edge = self.sg.edges.get((cur, nxt))
                if not edge:
                    break
                travel = edge.length / 1.3
                if budget >= travel:
                    budget -= travel
                    cur = nxt
                    gx, gy = self.sg.nodes[cur].x, self.sg.nodes[cur].y
                else:
                    frac = budget / max(travel, 0.01)
                    gx = self.sg.nodes[cur].x + (self.sg.nodes[nxt].x - self.sg.nodes[cur].x) * frac
                    gy = self.sg.nodes[cur].y + (self.sg.nodes[nxt].y - self.sg.nodes[cur].y) * frac
                    budget = 0
            ghosts.append({"id": agent.id, "x": round(gx, 1), "y": round(gy, 1), "ghost": True})
        return ghosts
