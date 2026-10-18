"""Reproducible multi-pair evaluation; writes evaluation_results.csv."""

from __future__ import annotations

import csv
import random
from pathlib import Path
from statistics import mean

from traffic_route import GRAPH, NODES, SCENARIOS, edge_cost, search, shortest_distance_path


def path_metrics(path: list[str], scenario: str) -> tuple[float, float]:
    total_time = 0.0
    total_distance = 0.0
    for source, target in zip(path, path[1:]):
        edge = next(e for e in GRAPH.adjacency[source] if e.target == target)
        total_time += edge_cost(edge, scenario)
        total_distance += edge.distance_km
    return total_time, total_distance


def make_pairs() -> list[tuple[str, str]]:
    pairs = [("J01", "J20"), ("J05", "J16"), ("J03", "J18"), ("J11", "J04")]
    rng = random.Random(20261017)
    candidates = [(a, b) for a in NODES for b in NODES if a != b and (a, b) not in pairs]
    rng.shuffle(candidates)
    seen = set(pairs)
    for pair in candidates:
        reverse = (pair[1], pair[0])
        if pair not in seen and reverse not in seen:
            pairs.append(pair)
            seen.add(pair)
        if len(pairs) == 30:
            break
    return pairs


def run_evaluation(output: str | Path = "evaluation_results.csv") -> tuple[int, dict[str, float]]:
    rows: list[dict[str, object]] = []
    pairs = make_pairs()
    all_agree = True
    total_improvement = 0.0
    improvement_count = 0

    # 30 OD pairs x four conditions yields 120 comparable route queries.
    for start, goal in pairs:
        for scenario in SCENARIOS:
            astar = search(start, goal, scenario)
            dijkstra = search(start, goal, scenario, use_heuristic=False)
            free_flow = search(start, goal, scenario, use_heuristic=False, free_flow=True)
            shortest = shortest_distance_path(start, goal, scenario)
            free_actual_min, free_distance = path_metrics(free_flow.path, scenario)
            # Average repeated timings to reduce timer noise on this small graph.
            astar.runtime_ms = mean(search(start, goal, scenario).runtime_ms for _ in range(7))
            dijkstra.runtime_ms = mean(search(start, goal, scenario, use_heuristic=False).runtime_ms for _ in range(7))
            agreement = abs(astar.travel_time_min - dijkstra.travel_time_min) < 1e-9
            all_agree = all_agree and agreement
            saved = free_actual_min - astar.travel_time_min
            total_improvement += saved
            improvement_count += 1

            rows.append({
                "scenario": scenario,
                "origin": start,
                "destination": goal,
                "astar_path": " -> ".join(astar.path),
                "astar_travel_time_min": round(astar.travel_time_min, 6),
                "astar_distance_km": round(astar.distance_km, 6),
                "astar_expanded_nodes": astar.expanded_nodes,
                "astar_runtime_ms": round(astar.runtime_ms, 6),
                "dijkstra_path": " -> ".join(dijkstra.path),
                "dijkstra_travel_time_min": round(dijkstra.travel_time_min, 6),
                "dijkstra_expanded_nodes": dijkstra.expanded_nodes,
                "dijkstra_runtime_ms": round(dijkstra.runtime_ms, 6),
                "free_flow_path": " -> ".join(free_flow.path),
                "free_flow_planning_time_min": round(free_flow.travel_time_min, 6),
                "free_flow_path_traffic_time_min": round(free_actual_min, 6),
                "free_flow_path_distance_km": round(free_distance, 6),
                "shortest_distance_path": " -> ".join(shortest.path),
                "shortest_distance_km": round(shortest.distance_km, 6),
                "shortest_distance_path_traffic_time_min": round(shortest.travel_time_min, 6),
                "traffic_saving_min": round(saved, 6),
                "optimal_cost_agreement": agreement,
            })

    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    summary = {
        "astar_mean_expanded_nodes": mean(int(r["astar_expanded_nodes"]) for r in rows),
        "dijkstra_mean_expanded_nodes": mean(int(r["dijkstra_expanded_nodes"]) for r in rows),
        "astar_mean_runtime_ms": mean(float(r["astar_runtime_ms"]) for r in rows),
        "dijkstra_mean_runtime_ms": mean(float(r["dijkstra_runtime_ms"]) for r in rows),
        "mean_traffic_saving_min": total_improvement / improvement_count,
        "optimal_cost_agreement_rate": 1.0 if all_agree else 0.0,
        "query_count": float(len(rows)),
        "pair_count": float(len(pairs)),
    }
    return len(rows), summary


if __name__ == "__main__":
    count, metrics = run_evaluation(Path(__file__).with_name("evaluation_results.csv"))
    print(f"Wrote {count} rows to evaluation_results.csv")
    for key, value in metrics.items():
        print(f"{key}: {value:.6f}")
