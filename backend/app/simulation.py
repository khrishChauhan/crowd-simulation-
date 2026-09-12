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
from .graph_model import StadiumGraph, build_stadium_graph, build_temple_graph, build_graph_for_level
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
    # 4-stage lifecycle: INGRESS | CENTER_DWELL | EGRESS | EVACUATED
    stage: str = "INGRESS"
    center_dwell_ticks: int = 0
    is_congested: bool = False


class SimulationEngine:
    def __init__(self, on_event=None, on_state=None, seed: int = None, arena_id: str = "player",
                 target_evacuation: int = 2000, difficulty: str = "novice",
                 center_dwell_sec: float = 2.5, level: int = 2):
        self.level = level
        self.sg: StadiumGraph = build_graph_for_level(level)
        self.predictor = SpatioTemporalCrowdPredictor()
        self.cameras = CameraManager()
        _seed = seed if seed is not None else config.DEMO_RANDOM_SEED
        self.rng = random.Random(_seed)
        self.seed = _seed
        self.arena_id = arena_id
        self.difficulty = difficulty
        self.center_dwell_sec = center_dwell_sec
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
        self.last_intervention_tick: int = 0
        # ---- Lifecycle node groups ----
        self._gate_nodes = [n for n, node in self.sg.nodes.items() if node.type == "GATE"]
        self._seating_nodes = [n for n, node in self.sg.nodes.items() if node.type == "SEATING"] or [n for n, node in self.sg.nodes.items() if node.type in ("CHECKPOINT", "JUNCTION")]
        self._exit_nodes = [n for n, node in self.sg.nodes.items() if node.type == "EXIT"]
        self._destinations = [n for n, node in self.sg.nodes.items() if node.type in DESTINATION_TYPES]
        # ---- Arena state counters ----
        self.evacuated_count: int = 0
        self.target_evacuation: int = target_evacuation
        self.penalty_seconds: float = 0.0
        self.incident_count: int = 0
        self.evacuation_log: List[dict] = []
        self.total_spawned: int = 0
        self.spawn_timer_sec: float = 0.0
        self.spawn_budget_this_second: int = 0
        # ---- Seed initial population ----
        self._seed_population(base_size=180 if level == 1 else 220)
        self.metrics_history: List[dict] = []

    def set_level(self, level: int):
        self.level = level
        self.sg = build_graph_for_level(level)
        self._gate_nodes = [n for n, node in self.sg.nodes.items() if node.type == "GATE"]
        self._seating_nodes = [n for n, node in self.sg.nodes.items() if node.type == "SEATING"] or [n for n, node in self.sg.nodes.items() if node.type in ("CHECKPOINT", "JUNCTION")]
        self._exit_nodes = [n for n, node in self.sg.nodes.items() if node.type == "EXIT"]
        self._destinations = [n for n, node in self.sg.nodes.items() if node.type in DESTINATION_TYPES]
        self.agents.clear()
        self.tick_count = 0
        self.evacuated_count = 0
        self.total_spawned = 0
        self.spawn_timer_sec = 0.0
        self.spawn_budget_this_second = 0
        self.penalty_seconds = 0.0
        self.incident_count = 0
        self.evacuation_log.clear()
        self.events.clear()
        self.decisions_log.clear()
        self.active_decision = None
        self._seed_population(base_size=180 if level == 1 else 220)

    # ---------------------------------------------------------------- setup
    def _seed_population(self, base_size: int):
        """Spawn an initial population entering through gates and concourses toward seating."""
        spawn_pool = self._gate_nodes * 2 + [n for n, node in self.sg.nodes.items() if node.type in ("CHECKPOINT", "JUNCTION")]
        for _ in range(base_size):
            node_id = self.rng.choice(spawn_pool)
            self._spawn_agent(node_id, stage="INGRESS")

    def _spawn_agent(self, at_node: str = None, destination: Optional[str] = None,
                     stage: str = "INGRESS"):
        from .graph_model import ALIAS_MAP
        if len(self.agents) >= config.MAX_PARTICLES:
            return None
        # Default spawn point: random perimeter gate
        if at_node is None:
            at_node = self.rng.choice(self._gate_nodes)
        at_node = ALIAS_MAP.get(at_node, at_node)
        # Default destination depends on stage
        if destination is None:
            if stage == "INGRESS":
                destination = self.rng.choice(self._seating_nodes)
            else:
                active_exits = self._get_active_exits()
                destination = self.rng.choice(active_exits) if active_exits else self.rng.choice(self._exit_nodes)
        destination = ALIAS_MAP.get(destination, destination)
        route = dynamic_astar(self.sg, at_node, destination) or [at_node]
        node = self.sg.nodes[at_node]
        aid = next(self._next_agent_id)
        agent = Agent(
            id=aid, x=node.x, y=node.y, current_node=at_node, route=route[1:], destination=destination,
            group_id=self.rng.randint(0, 5),
            response_delay=self.rng.randint(1, 6),
            compliance_probability=self.rng.uniform(0.65, 0.97),
            lateral_offset=self.rng.uniform(-3.5, 3.5),
            stage=stage,
        )
        self.agents[aid] = agent
        self.total_spawned += 1
        return agent

    def _get_active_exits(self) -> List[str]:
        """Return exits that have at least one unblocked incoming corridor."""
        active = []
        for eid in self._exit_nodes:
            for pred in self.sg.predecessors(eid):
                edge = self.sg.edges.get((pred, eid))
                if edge and edge.enabled and edge.control_state != "BLOCK":
                    active.append(eid)
                    break
        return active if active else list(self._exit_nodes)

    # -------------------------------------------------------------- ticking
    def step(self):
        dt = config.SIMULATION_DT
        self.tick_count += 1
        self._move_agents(dt)
        self._recompute_occupancy()

        # -----------------------------------------------------------------
        # True Random Trickle Spawner (Every second)
        # -----------------------------------------------------------------
        if self.total_spawned < self.target_evacuation:
            # 1-second countdown bucket
            if not hasattr(self, 'spawn_timer_sec') or self.spawn_timer_sec <= 0:
                self.spawn_timer_sec = 1.0
                # Randomize how many people spawn this entire second 
                # (e.g., sometimes 0, sometimes 5, sometimes 6)
                self.spawn_budget_this_second = self.rng.randint(0, 7)
            
            self.spawn_timer_sec -= dt
            
            # Distribute this second's budget randomly across its remaining ticks
            ticks_left = max(1, int(self.spawn_timer_sec / dt))
            chance_per_tick = self.spawn_budget_this_second / ticks_left
            
            spawn_count = 0
            if self.rng.random() < chance_per_tick:
                spawn_count = 1
                self.spawn_budget_this_second -= 1
            
            spawn_count = min(spawn_count, self.target_evacuation - self.total_spawned)
            
            for _ in range(spawn_count):
                if len(self.agents) < config.MAX_PARTICLES:
                    gate = self.rng.choice(self._gate_nodes)
                    # If the player clicked the Gate to BLOCK it, skip spawning!
                    if self.sg.nodes[gate].control_state != "BLOCK":
                        seating = self.rng.choice(self._seating_nodes)
                        self._spawn_agent(gate, destination=seating, stage="INGRESS")

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

        # ---- Immediate evacuation if agent reaches an EXIT node ----
        if node.type == "EXIT":
            agent.stage = "EVACUATED"
            self.evacuated_count += 1
            self.evacuation_log.append({
                "agent_id": agent.id,
                "exit": agent.current_node,
                "tick": self.tick_count,
                "timestamp": time.time(),
            })
            if len(self.evacuation_log) > 500:
                self.evacuation_log.pop(0)
            if agent.id in self.agents:
                del self.agents[agent.id]
            return

        # ---- Stage 2: CENTER_DWELL — mandatory 2.5s dwell at seating ----
        if agent.stage == "CENTER_DWELL":
            if agent.center_dwell_ticks > 0:
                agent.center_dwell_ticks -= 1
                return
            # Dwell expired → transition to Stage 3 EGRESS
            agent.stage = "EGRESS"
            active_exits = self._get_active_exits()
            exit_dest = self.rng.choice(active_exits) if active_exits else self.rng.choice(self._exit_nodes)
            egress_route = dynamic_astar(self.sg, agent.current_node, exit_dest)
            if egress_route:
                agent.destination = exit_dest
                agent.route = egress_route[1:]
            return

        # ---- Checkpoint HOLD: agents wait, do not depart while dwell_ticks remain ----
        if node.control_state == "HOLD" and agent.dwell_ticks <= 0:
            agent.dwell_ticks = 3
        if agent.dwell_ticks > 0:
            agent.dwell_ticks -= 1
            return

        if not agent.route:
            if agent.stage == "INGRESS" and agent.current_node == agent.destination:
                # Arrived at seating center → begin mandatory dwell (Stage 2)
                agent.stage = "CENTER_DWELL"
                agent.center_dwell_ticks = int(self.center_dwell_sec / config.SIMULATION_DT)  # 5 ticks @ 0.5s = 2.5s
                return
            elif agent.stage == "EGRESS":
                # Route expired but not at exit yet — reroute to active exit
                active_exits = self._get_active_exits()
                exit_dest = self.rng.choice(active_exits) if active_exits else self.rng.choice(self._exit_nodes)
                egress_route = dynamic_astar(self.sg, agent.current_node, exit_dest)
                if egress_route:
                    agent.destination = exit_dest
                    agent.route = egress_route[1:]
            else:
                # Reroute toward current destination
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
        elif cs == "SPLIT_FLOW" and self.rng.random() < agent.compliance_probability:
            from .flow_optimizer import min_cost_flow_split
            active_exits = self._get_active_exits()
            if len(active_exits) > 1:
                split = min_cost_flow_split(self.sg, agent.current_node, active_exits, demand=float(len(self.agents)))
                best_exit = max(split.keys(), key=lambda ex: split.get(ex, 0.0))
                if best_exit != agent.destination:
                    rerouted = dynamic_astar(self.sg, agent.current_node, best_exit)
                    if rerouted and len(rerouted) > 1:
                        agent.destination = best_exit
                        agent.route = rerouted[1:]
                        next_node_id = agent.route[0]
                        edge = self.sg.edges.get((agent.current_node, next_node_id))

        target_node = self.sg.nodes.get(next_node_id)
        if edge is None or not edge.enabled or edge.control_state == "BLOCK" or (target_node and target_node.control_state == "BLOCK"):
            rerouted = dynamic_astar(self.sg, agent.current_node, agent.destination)
            if rerouted and len(rerouted) > 1:
                agent.route = rerouted[1:]
                next_node_id = agent.route[0]
                edge = self.sg.edges.get((agent.current_node, next_node_id))
            else:
                # If current destination is blocked or unreachable, try any other active exit if in EGRESS
                if agent.stage in ("EGRESS", "CENTER_DWELL"):
                    active_exits = self._get_active_exits()
                    for alt_ex in active_exits:
                        if alt_ex != agent.destination and self.sg.nodes[alt_ex].control_state != "BLOCK":
                            alt_route = dynamic_astar(self.sg, agent.current_node, alt_ex)
                            if alt_route and len(alt_route) > 1:
                                agent.destination = alt_ex
                                agent.route = alt_route[1:]
                                next_node_id = agent.route[0]
                                edge = self.sg.edges.get((agent.current_node, next_node_id))
                                break
                if edge is None or not edge.enabled or edge.control_state == "BLOCK":
                    return  # no feasible route right now; wait this tick

        if edge is None:
            return

        # Commit to the move
        agent.target_node = next_node_id
        agent.progress = 0.0
        agent.route.pop(0)
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
        dst = self.sg.nodes[agent.target_node]

        # Check if corridor or destination became BLOCKED while in transit
        if edge.control_state == "BLOCK" or not edge.enabled or dst.control_state == "BLOCK":
            # If agent has progressed less than 60%, turn around back to current_node
            if agent.progress < 0.6:
                agent.current_node, agent.target_node = agent.target_node, agent.current_node
                agent.progress = max(0.0, 1.0 - agent.progress)
                agent.route = []
                return
            else:
                speed_factor = 0.0
                agent.is_congested = True
        else:
            occupancy = self.edge_live_count.get((agent.current_node, agent.target_node), 1) if hasattr(self, "edge_live_count") else 1
            
            reference_L = 50.0 
            scaled_half_capacity = 10.0 * (max(edge.length, 1.0) / reference_L)
            
            density_ratio = occupancy / max(scaled_half_capacity, 1.0)
            speed_factor = 1.0 / (1.0 + density_ratio)
            speed_factor = max(0.5, speed_factor)
            
            # 1. NEW: Flag the agent as congested if speed drops below 65% of max
            agent.is_congested = (speed_factor <= 0.65)
            
        # 2. USER RULE: Lower the speed from 18.0 to 10.0 (the sweet spot)
        base_speed = 10.0 
        agent.progress += (base_speed * speed_factor * dt * 2.5) / max(edge.length, 1.0)

        src = self.sg.nodes[agent.current_node]
        dst = self.sg.nodes[agent.target_node]
        t = min(1.0, agent.progress)
        agent.x = src.x + (dst.x - src.x) * t
        agent.y = src.y + (dst.y - src.y) * t

        if agent.progress >= 1.0:
            arrived_at = agent.target_node
            self.arrival_counters[arrived_at] = self.arrival_counters.get(arrived_at, 0) + 1
            agent.current_node = arrived_at
            agent.target_node = None
            agent.progress = 0.0

            # ---- Stage 4: EVACUATED — agent exits the arena ----
            if self.sg.nodes[arrived_at].type == "EXIT":
                agent.stage = "EVACUATED"
                self.evacuated_count += 1
                self.evacuation_log.append({
                    "agent_id": agent.id,
                    "exit": arrived_at,
                    "tick": self.tick_count,
                    "timestamp": time.time(),
                })
                if len(self.evacuation_log) > 500:
                    self.evacuation_log.pop(0)
                del self.agents[agent.id]
                return

            # ---- Stage 1→2 transition: INGRESS agent arrived at seating ----
            if agent.stage == "INGRESS" and self.sg.nodes[arrived_at].type == "SEATING":
                agent.stage = "CENTER_DWELL"
                agent.center_dwell_ticks = int(self.center_dwell_sec / config.SIMULATION_DT)  # 5 ticks @ 0.5s = 2.5s

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
    def update_predictions(self):
        """Update time-based density predictions across horizons."""
        for nid, node in self.sg.nodes.items():
            current_density = node.current_density
            flow_rate = node.inflow - node.outflow
            for horizon in config.PREDICTION_HORIZONS:
                future = current_density + (flow_rate * horizon * 0.1)
                node.predicted_density[horizon] = max(0.0, round(future, 2))

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
        self.update_predictions()

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

        # 5. HAZARD CHECK (50% crowd crush hazard check & +3s penalty)
        self._check_hazards()

        # 6. Identify risk location(s) and OPTIMIZE + CONTROL
        self._auto_reopen()
        self._evaluate_and_intervene()

    def _check_hazards(self):
        """Evaluate checkpoints and junctions for crowd crush hazard incidents."""
        for nid, node in self.sg.nodes.items():
            if node.type in ("CHECKPOINT", "JUNCTION"):
                ratio = node.current_people / max(node.capacity * config.HAZARD_CAPACITY_MULTIPLIER, 1.0)
                if ratio >= config.HAZARD_OCCUPANCY_THRESHOLD:
                    if self.rng.random() < config.HAZARD_TRIGGER_PROBABILITY:
                        self.penalty_seconds += config.HAZARD_PENALTY_SECONDS
                        self.incident_count += 1
                        self._log_event(
                            event_type="HAZARD_INCIDENT",
                            message=f"CRUSH ALERT at {node.id}! +3s Penalty added.",
                            payload={
                                "node": node.id,
                                "penalty": config.HAZARD_PENALTY_SECONDS,
                                "total_penalties": round(self.penalty_seconds, 1),
                                "density": round(node.current_density, 2),
                            }
                        )

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
        # Enforce tier-specific replan interval (latency)
        replan_interval = config.AI_REPLAN_INTERVAL_TICKS.get(self.difficulty, 4)
        if self.difficulty == "super_predictive":
            replan_interval = 1  # Override to 1 tick (0.5s) for instant reaction time

        if self.tick_count - self.last_intervention_tick < replan_interval:
            return

        allowed = config.AI_ALLOWED_ACTIONS.get(self.difficulty)

        # ==========================================
        # 1. UNIVERSAL PROACTIVE METERING (ALL LEVELS)
        # ==========================================
        # All AI levels now check the 30% criteria to choke spawn rates.
        # However, Novice checks it every 5s, Pro every 2s, Master every 0.5s.
        for nid, node in self.sg.nodes.items():
            if node.type in ("CHECKPOINT", "JUNCTION"):
                hazard_ceil = node.capacity * config.HAZARD_CAPACITY_MULTIPLIER
                # Changed from 45% to 30% criteria for all AI levels
                if (node.current_people / max(hazard_ceil, 1.0)) >= 0.30:
                    for entry_id in ("ENTRY-01", "ENTRY-02", "ENTRY-03"):
                        entry_node = self.sg.nodes.get(entry_id)
                        if entry_node and entry_node.control_state == "NORMAL":
                            decision = Decision(
                                risk_location=nid,
                                intervention_location=entry_id,
                                action="HOLD",
                                duration_sec=3.0,
                                reason=f"{self.difficulty.upper()} AI: Holding {entry_id} to prevent hazard at {nid}",
                                confidence=0.99,
                                risk_without_action=0.95,
                                risk_with_action=0.05,
                                safe=True,
                            )
                            self.last_intervention_tick = self.tick_count
                            self.decisions_log.append(decision.to_dict())
                            if len(self.decisions_log) > 50:
                                self.decisions_log.pop(0)
                            if not self.shadow_mode:
                                self._apply_action(decision)
                            self._log_event(
                                "PROACTIVE_METERING",
                                f"[{self.difficulty.upper()} AI] Proactively holding {entry_id} for 3.0s (30% threshold hit at {nid})",
                                decision.to_dict(),
                            )
                            self.active_decision = decision
                            return

        # ==========================================
        # 2. ADVANCED TACTICS (PRO & SUPER PREDICTIVE ONLY)
        # ==========================================
        if self.difficulty in ("pro", "super_predictive"):
            # Multi-Exit Dynamic Load Balancer
            exit_nodes = [e for e in ("EXIT-01", "EXIT-02", "EXIT-03") if e in self.sg.nodes]
            if len(exit_nodes) == 3:
                exit_loads = {e: 0 for e in exit_nodes}
                for a in self.agents.values():
                    if a.stage == "EGRESS" and a.destination in exit_loads:
                        exit_loads[a.destination] += 1
                for e in exit_nodes:
                    exit_loads[e] += int(self.sg.nodes[e].current_people)

                total_exit_load = sum(exit_loads.values())
                if total_exit_load >= 50:
                    max_e = max(exit_loads, key=exit_loads.get)
                    min_e = min(exit_loads, key=exit_loads.get)
                    max_pct = exit_loads[max_e] / total_exit_load
                    min_pct = exit_loads[min_e] / total_exit_load

                    # Rebalance threshold
                    if max_pct > 0.38 and min_pct < 0.28:
                        iv_loc = "JUNCTION-02" if max_e == "EXIT-01" else ("JUNCTION-03" if max_e == "EXIT-03" else "JUNCTION-02")
                        act = "REDIRECT_LEFT" if min_e < max_e else "REDIRECT_RIGHT"
                        decision = Decision(
                            risk_location=max_e,
                            intervention_location=iv_loc,
                            action=act,
                            duration_sec=5.0,
                            reason=f"AI Tri-Exit Optimizer: Rebalancing flow from {max_e} to {min_e}",
                            confidence=0.98,
                            risk_without_action=0.85,
                            risk_with_action=0.15,
                            safe=True,
                        )
                        self.last_intervention_tick = self.tick_count
                        self.decisions_log.append(decision.to_dict())
                        if len(self.decisions_log) > 50:
                            self.decisions_log.pop(0)
                        if not self.shadow_mode:
                            self._apply_action(decision)

                        count_diverted = 0
                        candidate_nodes = set(nid for nid in self.sg.nodes.keys() if "JUNCTION" in nid or "HUB" in nid or "PLAZA" in nid or "BYPASS" in nid)
                        
                        target_diversion = int((exit_loads[max_e] - exit_loads[min_e]) / 2)
                        
                        for a in self.agents.values():
                            if a.stage == "EGRESS" and a.destination == max_e:
                                if a.current_node in candidate_nodes:
                                    new_rt = dynamic_astar(self.sg, a.current_node, min_e)
                                    if new_rt:
                                        a.destination = min_e
                                        a.route = new_rt[1:]
                                        count_diverted += 1
                                        if count_diverted >= target_diversion or count_diverted >= 250:
                                            break
                        
                        self._log_event("AI_TRI_EXIT_BALANCING", f"[{self.difficulty.upper()} AI] Diverted {count_diverted} agents from {max_e} to {min_e}", decision.to_dict())
                        self.active_decision = decision
                        return

        # ==========================================
        # 3. ANTICIPATORY PREDICTIONS (SUPER PREDICTIVE ONLY)
        # ==========================================
        if self.difficulty == "super_predictive":
            anticipatory = [
                (nid, n) for nid, n in self.sg.nodes.items()
                if n.type in ("CHECKPOINT", "JUNCTION") and n.predicted_density.get(30, 0.0) >= 3.0
            ]
            if anticipatory:
                anticipatory.sort(key=lambda kv: kv[1].predicted_density.get(30, 0.0), reverse=True)
                pred_node_id, pred_node = anticipatory[0]
                decision = plan_intervention(self.sg, pred_node_id, allowed_actions=allowed, horizon=30)

                if decision.safe and decision.action != "DO_NOTHING" and decision.intervention_location:
                    self.last_intervention_tick = self.tick_count
                    self.decisions_log.append(decision.to_dict())
                    if len(self.decisions_log) > 50:
                        self.decisions_log.pop(0)
                    self._log_event(
                        "PROACTIVE_INTERVENTION",
                        f"[SUPER-PREDICTIVE] Anticipated bottleneck at {pred_node_id} (+30s). "
                        f"Proactively deploying {decision.action} at upstream {decision.intervention_location}",
                        decision.to_dict()
                    )
                    if not self.shadow_mode:
                        self._apply_action(decision)
                    self.active_decision = decision
                    return

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

            decision = plan_intervention(self.sg, risk_node_id, allowed_actions=allowed, horizon=60)
            self.last_intervention_tick = self.tick_count
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
        from .graph_model import ALIAS_MAP
        gate_id = ALIAS_MAP.get(gate_id, gate_id)
        for _ in range(count):
            seating_dest = self.rng.choice(self._seating_nodes)
            self._spawn_agent(gate_id, destination=seating_dest, stage="INGRESS")
        self._log_event("SCENARIO_TRIGGER", f"Crowd surge injected at {gate_id} (+{count} people)",
                         {"gate": gate_id, "count": count})

    def close_exit(self, exit_id: str = "EXIT-03"):
        from .graph_model import ALIAS_MAP
        exit_id = ALIAS_MAP.get(exit_id, exit_id)
        if exit_id in self.sg.nodes:
            self.sg.nodes[exit_id].control_state = "BLOCK"
        for pred in self.sg.predecessors(exit_id):
            edge = self.sg.edges.get((pred, exit_id))
            if edge:
                edge.enabled = False
                edge.control_state = "BLOCK"
        self._log_event("SCENARIO_TRIGGER", f"{exit_id} CLOSED - recalculating downstream routes",
                         {"exit": exit_id})
        # Recalculate routes for any agents targeting this closed exit
        active_exits = self._get_active_exits()
        if active_exits:
            for agent in self.agents.values():
                if agent.stage == "EGRESS" and agent.destination == exit_id:
                    alt_exit = self.rng.choice(active_exits)
                    start_at = agent.target_node if agent.target_node else agent.current_node
                    rerouted = dynamic_astar(self.sg, start_at, alt_exit)
                    if rerouted:
                        agent.destination = alt_exit
                        agent.route = rerouted[1:]
        for nid in self.sg.predecessors(exit_id):
            chain = predict_cascade(self.sg, nid)
            if chain:
                self._log_event("CROWD_CASCADE_RISK", f"Cascade risk chain: {' -> '.join(chain)}",
                                 {"chain": chain})

    def reopen_exit(self, exit_id: str = "EXIT-03"):
        from .graph_model import ALIAS_MAP
        exit_id = ALIAS_MAP.get(exit_id, exit_id)
        if exit_id in self.sg.nodes:
            self.sg.nodes[exit_id].control_state = "NORMAL"
        for pred in self.sg.predecessors(exit_id):
            edge = self.sg.edges.get((pred, exit_id))
            if edge:
                edge.enabled = True
                edge.control_state = "NORMAL"
        self._log_event("SCENARIO_TRIGGER", f"{exit_id} reopened", {"exit": exit_id})

    def trigger_counterflow(self):
        from .graph_model import ALIAS_MAP
        plaza_east = ALIAS_MAP.get("SEATING-C", "PLAZA-EAST")
        entry_1 = ALIAS_MAP.get("GATE-01", "ENTRY-01")
        seating_c_agents = [a for a in self.agents.values() if a.current_node in (plaza_east, "SEATING-C", "PLAZA-EAST")][:40]
        for agent in seating_c_agents:
            agent.stage = "EGRESS"
            agent.destination = entry_1
            route = dynamic_astar(self.sg, agent.current_node, entry_1)
            if route:
                agent.route = route[1:]
        for _ in range(40):
            self._spawn_agent(entry_1, destination=plaza_east, stage="INGRESS")
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
        self.__init__(on_event=self.on_event, on_state=self.on_state,
                      seed=self.seed, arena_id=self.arena_id,
                      target_evacuation=self.target_evacuation,
                      difficulty=self.difficulty,
                      center_dwell_sec=self.center_dwell_sec,
                      level=getattr(self, "level", 2))
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
             "ghost": a.ghost, "destination": a.destination, "route": a.route,
             "stage": a.stage, "is_congested": getattr(a, "is_congested", False)}
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
            # ---- Arena lifecycle counters ----
            "evacuated_count": self.evacuated_count,
            "target_evacuation": self.target_evacuation,
            "penalty_seconds": round(self.penalty_seconds, 1),
            "incident_count": self.incident_count,
            "total_spawned": self.total_spawned,
            "difficulty": self.difficulty,
        }

        return {
            "type": "crowd_state",
            "arena_id": self.arena_id,
            "difficulty": self.difficulty,
            "level": getattr(self, "level", 2),
            "evacuated_count": self.evacuated_count,
            "target_evacuation": self.target_evacuation,
            "penalty_seconds": round(self.penalty_seconds, 1),
            "incident_count": self.incident_count,
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
