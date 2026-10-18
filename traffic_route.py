"""Traffic-aware routing on a deterministic synthetic road network.

This project uses only the Python standard library. Road costs are estimated
travel times in minutes; traffic is simulated, not fetched from a live service.
"""

from __future__ import annotations

from dataclasses import dataclass
from heapq import heappop, heappush
from itertools import count
from math import hypot
from time import perf_counter


@dataclass(frozen=True)
class Edge:
    edge_id: str
    segment_id: str
    source: str
    target: str
    distance_km: float
    speed_kmh: float
    central: bool


@dataclass
class SearchResult:
    path: list[str]
    travel_time_min: float
    distance_km: float
    expanded_nodes: int
    runtime_ms: float


class RoadGraph:
    def __init__(self) -> None:
        self.coordinates: dict[str, tuple[float, float]] = {}
        self.adjacency: dict[str, list[Edge]] = {}
        self.edges: list[Edge] = []
        self.max_speed_kmh = 0.0

    def add_edge(self, edge: Edge) -> None:
        self.edges.append(edge)
        self.adjacency.setdefault(edge.source, []).append(edge)
        self.adjacency.setdefault(edge.target, [])
        self.max_speed_kmh = max(self.max_speed_kmh, edge.speed_kmh)


TRAFFIC_FACTORS = {
    "light": {"central": 1.15, "outer": 1.00},
    "moderate": {"central": 1.75, "outer": 1.20},
    "heavy": {"central": 2.60, "outer": 1.55},
    "incident": {"central": 2.00, "outer": 1.25},
}


def build_graph() -> RoadGraph:
    """Create a 5 by 4 junction grid (20 nodes, 36 physical segments).

    Most segments are two-way. A few are one-way to make the graph realistic;
    all nodes remain reachable through alternate streets.
    """
    graph = RoadGraph()
    for row in range(4):
        for col in range(5):
            node = f"J{row * 5 + col + 1:02d}"
            graph.coordinates[node] = (float(col), float(3 - row))
            graph.adjacency[node] = []

    segment_no = 0

    def add_segment(a: str, b: str, speed: float, one_way: str | None = None,
                    road_length_km: float | None = None) -> None:
        nonlocal segment_no
        segment_no += 1
        segment_id = f"S{segment_no:02d}"
        ax, ay = graph.coordinates[a]
        bx, by = graph.coordinates[b]
        length = road_length_km if road_length_km is not None else hypot(bx - ax, by - ay)
        # Roads in the central blocks are higher capacity, but more affected by traffic.
        central = (
            1 <= round(ax) <= 3 and 1 <= round(bx) <= 3
            and 0 <= round(ay) <= 3 and 0 <= round(by) <= 3
        )

        def arc(source: str, target: str) -> None:
            suffix = "F" if source == a else "R"
            graph.add_edge(Edge(f"R{segment_no:02d}{suffix}", segment_id, source,
                                target, length, speed, central))

        arc(a, b)
        if one_way is None or one_way == "reverse":
            arc(b, a)

    # Horizontal roads; the second row is a one-way eastbound corridor.
    for row in range(4):
        for col in range(4):
            a = f"J{row * 5 + col + 1:02d}"
            b = f"J{row * 5 + col + 2:02d}"
            speed = 50.0 if row in (1, 2) else 40.0
            add_segment(a, b, speed, "forward" if row == 1 else None)

    # Vertical roads; the middle column is one-way southbound.
    for row in range(3):
        for col in range(5):
            a = f"J{row * 5 + col + 1:02d}"
            b = f"J{(row + 1) * 5 + col + 1:02d}"
            speed = 45.0 if col in (1, 2, 3) else 35.0
            add_segment(a, b, speed, "forward" if col == 2 else None)

    # Four diagonal connectors provide faster alternatives around congestion.
    for a, b in (("J02", "J07"), ("J04", "J09"), ("J12", "J17"), ("J14", "J19")):
        add_segment(a, b, 60.0)

    # A high-speed expressway is longer than the direct geometric distance.
    # It provides the controlled case where minimum time differs from distance.
    add_segment("J01", "J20", 100.0, road_length_km=7.2)

    return graph


GRAPH = build_graph()
NODES = tuple(sorted(GRAPH.coordinates))
SCENARIOS = tuple(TRAFFIC_FACTORS)
INCIDENT_CLOSED_SEGMENTS = frozenset({"S08"})


def edge_cost(edge: Edge, scenario: str, *, free_flow: bool = False,
              apply_incident_closure: bool = True) -> float:
    if scenario not in TRAFFIC_FACTORS:
        raise ValueError(f"Unknown scenario: {scenario}")
    if apply_incident_closure and scenario == "incident" and edge.segment_id in INCIDENT_CLOSED_SEGMENTS:
        return float("inf")
    factor = 1.0 if free_flow else TRAFFIC_FACTORS[scenario]["central" if edge.central else "outer"]
    return edge.distance_km / edge.speed_kmh * 60.0 * factor


def heuristic(node: str, goal: str) -> float:
    x1, y1 = GRAPH.coordinates[node]
    x2, y2 = GRAPH.coordinates[goal]
    return hypot(x2 - x1, y2 - y1) / GRAPH.max_speed_kmh * 60.0


def search(start: str, goal: str, scenario: str, *, use_heuristic: bool = True,
           free_flow: bool = False, apply_incident_closure: bool = True) -> SearchResult:
    """Run A* (or Dijkstra when use_heuristic=False) and return route metrics."""
    if start not in GRAPH.coordinates or goal not in GRAPH.coordinates:
        raise ValueError("Start and destination must be valid junction IDs")
    if scenario not in TRAFFIC_FACTORS:
        raise ValueError(f"Unknown scenario: {scenario}")
    started = perf_counter()
    serial = count()
    frontier: list[tuple[float, int, float, str]] = []
    heappush(frontier, (heuristic(start, goal) if use_heuristic else 0.0,
                        next(serial), 0.0, start))
    best_cost = {start: 0.0}
    previous: dict[str, tuple[str, Edge]] = {}
    expanded = 0

    while frontier:
        _, _, queued_cost, current = heappop(frontier)
        if queued_cost > best_cost[current] + 1e-12:
            continue
        current_cost = queued_cost
        if current == goal:
            break
        expanded += 1
        for edge in GRAPH.adjacency[current]:
            cost = edge_cost(edge, scenario, free_flow=free_flow,
                             apply_incident_closure=apply_incident_closure)
            if cost == float("inf"):
                continue
            candidate = current_cost + cost
            if candidate + 1e-12 < best_cost.get(edge.target, float("inf")):
                best_cost[edge.target] = candidate
                previous[edge.target] = (current, edge)
                estimate = heuristic(edge.target, goal) if use_heuristic else 0.0
                heappush(frontier, (candidate + estimate, next(serial), candidate, edge.target))

    if goal not in best_cost:
        raise ValueError(f"No route from {start} to {goal} in {scenario} scenario")

    path = [goal]
    route_edges: list[Edge] = []
    current = goal
    while current != start:
        parent, edge = previous[current]
        route_edges.append(edge)
        path.append(parent)
        current = parent
    path.reverse()
    route_edges.reverse()
    elapsed_ms = (perf_counter() - started) * 1000.0
    return SearchResult(path, best_cost[goal], sum(e.distance_km for e in route_edges),
                        expanded, elapsed_ms)


def route_summary(start: str, goal: str, scenario: str) -> dict[str, SearchResult]:
    return {
        "A* traffic-aware": search(start, goal, scenario),
        "Dijkstra traffic-aware": search(start, goal, scenario, use_heuristic=False),
        "Dijkstra free-flow": search(start, goal, scenario, use_heuristic=False, free_flow=True),
    }


def shortest_distance_path(start: str, goal: str, scenario: str, *,
                           apply_incident_closure: bool = True) -> SearchResult:
    """Find the distance-minimum path, then measure it at scenario travel costs."""
    if start not in GRAPH.coordinates or goal not in GRAPH.coordinates:
        raise ValueError("Start and destination must be valid junction IDs")
    if scenario not in TRAFFIC_FACTORS:
        raise ValueError(f"Unknown scenario: {scenario}")
    started = perf_counter()
    serial = count()
    frontier: list[tuple[float, int, str]] = [(0.0, next(serial), start)]
    best_distance = {start: 0.0}
    previous: dict[str, tuple[str, Edge]] = {}
    expanded = 0
    while frontier:
        distance, _, current = heappop(frontier)
        if distance > best_distance[current] + 1e-12:
            continue
        if current == goal:
            break
        expanded += 1
        for edge in GRAPH.adjacency[current]:
            # A distance-minimum route must still obey scenario road closures.
            if edge_cost(edge, scenario, free_flow=True,
                         apply_incident_closure=apply_incident_closure) == float("inf"):
                continue
            candidate = distance + edge.distance_km
            if candidate + 1e-12 < best_distance.get(edge.target, float("inf")):
                best_distance[edge.target] = candidate
                previous[edge.target] = (current, edge)
                heappush(frontier, (candidate, next(serial), edge.target))
    if goal not in best_distance:
        raise ValueError(f"No route from {start} to {goal} in {scenario} scenario")
    path = [goal]
    route_edges: list[Edge] = []
    current = goal
    while current != start:
        parent, edge = previous[current]
        path.append(parent)
        route_edges.append(edge)
        current = parent
    path.reverse()
    route_edges.reverse()
    elapsed_ms = (perf_counter() - started) * 1000.0
    return SearchResult(path, sum(edge_cost(e, scenario, apply_incident_closure=apply_incident_closure)
                                  for e in route_edges),
                        sum(e.distance_km for e in route_edges), expanded, elapsed_ms)


def result_as_dict(result: SearchResult) -> dict[str, object]:
    return {
        "path": result.path,
        "travel_time_min": result.travel_time_min,
        "distance_km": result.distance_km,
        "expanded_nodes": result.expanded_nodes,
        "runtime_ms": result.runtime_ms,
    }

