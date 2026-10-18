"""Local web interface for the traffic-aware route optimization demo."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from traffic_route import (
    GRAPH,
    NODES,
    SCENARIOS,
    TRAFFIC_FACTORS,
    edge_cost,
    route_summary,
    search,
    shortest_distance_path,
)


WEB_DIR = Path(__file__).resolve().parent / "web"
MIME_TYPES = {".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8"}


def route_payload(origin: str, destination: str, scenario: str) -> dict[str, object]:
    """Build the user-facing route, network, and baseline comparison payload."""
    if origin not in NODES or destination not in NODES:
        raise ValueError("Choose a valid origin and destination junction.")
    if scenario not in SCENARIOS:
        raise ValueError("Choose one of the listed traffic scenarios.")

    results = route_summary(origin, destination, scenario)
    recommended = results["A* traffic-aware"]
    free_flow = results["Dijkstra free-flow"]
    shortest = shortest_distance_path(origin, destination, scenario)

    def current_traffic_time(path: list[str]) -> float:
        total = 0.0
        for source, target in zip(path, path[1:]):
            edge = next(edge for edge in GRAPH.adjacency[source] if edge.target == target)
            total += edge_cost(edge, scenario)
        return total

    comparison = [
        {
            "method": "A* · traffic-aware",
            "time": recommended.travel_time_min,
            "distance": recommended.distance_km,
            "expanded": recommended.expanded_nodes,
            "runtime": recommended.runtime_ms,
            "note": "Recommended",
        },
        {
            "method": "Dijkstra · traffic-aware",
            "time": results["Dijkstra traffic-aware"].travel_time_min,
            "distance": results["Dijkstra traffic-aware"].distance_km,
            "expanded": results["Dijkstra traffic-aware"].expanded_nodes,
            "runtime": results["Dijkstra traffic-aware"].runtime_ms,
            "note": "Optimality check",
        },
        {
            "method": "Dijkstra · free-flow",
            "time": current_traffic_time(free_flow.path),
            "distance": free_flow.distance_km,
            "expanded": free_flow.expanded_nodes,
            "runtime": free_flow.runtime_ms,
            "note": "Planned without congestion",
        },
        {
            "method": "Shortest distance",
            "time": shortest.travel_time_min,
            "distance": shortest.distance_km,
            "expanded": shortest.expanded_nodes,
            "runtime": shortest.runtime_ms,
            "note": "Measured at current traffic",
        },
    ]

    segment_counts: dict[str, int] = {}
    for edge in GRAPH.edges:
        segment_counts[edge.segment_id] = segment_counts.get(edge.segment_id, 0) + 1
    edges = []
    for edge in GRAPH.edges:
        if edge.edge_id.endswith("R"):
            continue
        edges.append(
            {
                "id": edge.segment_id,
                "from": edge.source,
                "to": edge.target,
                "distance": edge.distance_km,
                "speed": edge.speed_kmh,
                "central": edge.central,
                "one_way": segment_counts[edge.segment_id] == 1,
                "closed": scenario == "incident" and edge.segment_id in {"S08"},
            }
        )

    return {
        "origin": origin,
        "destination": destination,
        "scenario": scenario,
        "route": recommended.path,
        "time": recommended.travel_time_min,
        "distance": recommended.distance_km,
        "expanded": recommended.expanded_nodes,
        "nodes": [
            {"id": node, "x": GRAPH.coordinates[node][0], "y": GRAPH.coordinates[node][1]}
            for node in NODES
        ],
        "edges": edges,
        "comparison": comparison,
        "traffic_factors": TRAFFIC_FACTORS[scenario],
        "closed_segments": ["S08"] if scenario == "incident" else [],
    }


class RouteHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        request = urlparse(self.path)
        if request.path == "/api/route":
            query = parse_qs(request.query)
            try:
                payload = route_payload(
                    query.get("origin", ["J05"])[0],
                    query.get("destination", ["J16"])[0],
                    query.get("scenario", ["heavy"])[0],
                )
                self._send(200, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")
            except (ValueError, StopIteration) as error:
                self._send(400, json.dumps({"error": str(error)}).encode("utf-8"),
                           "application/json; charset=utf-8")
            return

        relative = "index.html" if request.path in ("", "/") else request.path.lstrip("/")
        path = (WEB_DIR / relative).resolve()
        if WEB_DIR not in path.parents or not path.is_file():
            self._send(404, b"Not found", "text/plain; charset=utf-8")
            return
        content_type = MIME_TYPES.get(path.suffix, "text/html; charset=utf-8")
        self._send(200, path.read_bytes(), content_type)

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        print(f"{self.address_string()} - {format % args}")


if __name__ == "__main__":
    address = ("127.0.0.1", 8000)
    print(f"Open http://{address[0]}:{address[1]}")
    ThreadingHTTPServer(address, RouteHandler).serve_forever()
