"""Land-aware Dijkstra routing using a conservative forecast envelope."""
from datetime import datetime, timedelta
import heapq
import math

import numpy as np
from global_land_mask import globe

from backend.ocean import DataUnavailable
from ml.risk_policy import LABELS, label_ids
from backend import boundary


def distance(a, b):
    lat1, lat2 = math.radians(a[0]), math.radians(b[0])
    dlat, dlon = lat2 - lat1, math.radians(b[1] - a[1])
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371.0088 * 2 * math.asin(min(1, math.sqrt(h)))


def ocean_clear(points):
    """Approximate coastal buffer using eight 600m offsets around each point."""
    pts = np.asarray(points, dtype=float)
    lat, lon = pts[:, 0], pts[:, 1]
    clear = ~globe.is_land(lat, lon)
    for angle in np.linspace(0, 2 * np.pi, 8, endpoint=False):
        clear &= ~globe.is_land(lat + 0.6 / 111.2 * np.sin(angle), lon + 0.6 / (111.2 * np.cos(np.radians(lat))) * np.cos(angle))
    return clear


def segment_clear(a, b):
    count = max(2, math.ceil(distance(a, b) / 0.25) + 1)
    return bool(ocean_clear(np.linspace(a, b, count)).all())


def make_grid(start, end):
    d = distance(start, end)
    if d < 1 or d > 160:
        raise ValueError("Select offshore points between 1 and 160 km apart.")
    if not ocean_clear([start, end]).all():
        raise ValueError("Place both points offshore, at least approximately 600 m from mapped land.")
    margin = max(0.10, min(0.30, d / 300))
    south, north = max(7, min(start[0], end[0]) - margin), min(14, max(start[0], end[0]) + margin)
    west, east = max(77, min(start[1], end[1]) - margin), min(81, max(start[1], end[1]) + margin)
    lats = np.linspace(south, north, min(43, max(9, math.ceil((north - south) / 0.025) + 1)))
    lons = np.linspace(west, east, min(43, max(9, math.ceil((east - west) / 0.025) + 1)))
    pts = [(float(a), float(b)) for a in lats for b in lons]
    water = ocean_clear(pts) & np.array([boundary.allowed(p) for p in pts])
    # Up to 25 forecast sample points across the search area; unknown cells stay blocked.
    sample = [(float(a), float(b)) for a in np.linspace(south, north, 5) for b in np.linspace(west, east, 5)]
    sample = [p for p, good in zip(sample, ocean_clear(sample)) if good]
    sample = list(dict.fromkeys([tuple(start), tuple(end), *sample]))
    return pts, water, len(lons), sample, [south, west, north, east]


def envelope(row, hours, wave_limit, wind_limit, prediction=None):
    begin = datetime.fromisoformat(row["current"]["time"])
    by_time = {datetime.fromisoformat(h["time"]): h for h in row["hourly"]}
    expected = [begin + timedelta(hours=i) for i in range(hours + 1)]
    if any(t not in by_time for t in expected):
        raise DataUnavailable("Hourly forecasts do not cover the complete trip window.")
    wave = max(by_time[t]["wave_m"] for t in expected)
    wind = max(by_time[t]["wind_ms"] for t in expected)
    if prediction and prediction.get("available") and hours >= 6:
        wave = max(wave, prediction.get("conservative_upper_m", prediction["upper_m"]))
    ratio = max(wave / wave_limit, wind / wind_limit)
    category = int(label_ids(wave, wind))
    if prediction and prediction.get("risk_prediction", {}).get("available"):
        category = max(category, prediction["risk_prediction"]["conservative_label_id"])
    return {"wave_m": wave, "wind_ms": wind, "risk": min(1.5, ratio), "blocked": ratio >= 1 or category == 2,
            "label": LABELS[category], "label_id": category, "hazard_score": category + min(1.5, ratio),
            "reason": "Wave limit" if wave / wave_limit >= wind / wind_limit else "Wind limit"}


def solve_graph(points, adjacency, risks, start, end, weight, max_hazard=float("inf")):
    queue = [(0.0, start)]
    costs, previous = {start: 0.0}, {}
    while queue:
        cost, node = heapq.heappop(queue)
        if cost > costs[node]:
            continue
        if node == end:
            path = [end]
            while path[-1] != start:
                path.append(previous[path[-1]])
            return list(reversed(path))
        for edge in adjacency[node]:
            nxt, length = edge[:2]
            if risks[nxt]["blocked"] or risks[node]["blocked"]:
                continue
            risk = edge[2] if len(edge) >= 3 else max(risks[node]["risk"], risks[nxt]["risk"])
            hazard = edge[3] if len(edge) >= 4 else risk
            if hazard > max_hazard:
                continue
            candidate = cost + length * (1 + weight * risk ** 2)
            if candidate < costs.get(nxt, float("inf")):
                costs[nxt], previous[nxt] = candidate, node
                heapq.heappush(queue, (candidate, nxt))
    return None


def safest_shortest(points, adjacency, risks, start, end, max_distance):
    """Minimum attainable peak hazard, then minimum distance, within the trip budget.

    Binary-search edge hazard thresholds. At each threshold Dijkstra finds the
    shortest permitted path; this also resolves whether the trip budget is met.
    """
    thresholds = sorted({edge[3] if len(edge) >= 4 else edge[2] for edges in adjacency for edge in edges})
    lo, hi, best = 0, len(thresholds)-1, None
    while lo <= hi:
        mid = (lo + hi) // 2
        path = solve_graph(points, adjacency, risks, start, end, 0, thresholds[mid])
        if path:
            lengths = [{edge[0]: edge[1] for edge in edges} for edges in adjacency]
            total = sum(lengths[a][b] for a,b in zip(path, path[1:]))
        else:
            total = float("inf")
        if total <= max_distance:
            best, hi = path, mid - 1
        else:
            lo = mid + 1
    return best


def combine_risks(items):
    worst = max(items, key=lambda r: r["risk"])
    category = max(r.get("label_id", 0) for r in items)
    ratio = max(r["risk"] for r in items)
    return {**worst, "wave_m": max(r["wave_m"] for r in items), "wind_ms": max(r["wind_ms"] for r in items),
            "label_id": category, "label": LABELS[category], "hazard_score": category + ratio,
            "blocked": any(r["blocked"] for r in items)}


def no_route_reason(start_risk, end_risk, wave_limit, wind_limit, shortest_km, hours, speed):
    """Explain observed constraints without relaxing the routing rules."""
    reasons = []
    for name, risk in [("Departure", start_risk), ("Destination", end_risk)]:
        if not risk["blocked"]:
            continue
        details = []
        if risk["wave_m"] >= wave_limit:
            details.append(f"wave envelope {risk['wave_m']:.2f} m meets or exceeds the {wave_limit:g} m limit")
        if risk["wind_ms"] >= wind_limit:
            details.append(f"wind envelope {risk['wind_ms']:.2f} m/s meets or exceeds the {wind_limit:g} m/s limit")
        if risk["label_id"] == 2:
            details.append("forecast or model classification is Danger")
        reasons.append(f"{name} area is excluded: {'; '.join(details)}.")
    if shortest_km is not None and shortest_km > hours * speed * 1.852:
        reasons.append(f"The shortest permitted path on this grid is {shortest_km:.1f} km "
                       f"({shortest_km / (speed * 1.852):.1f} h at {speed:g} knots), "
                       f"beyond the selected {hours:g} h trip window.")
    if not reasons:
        reasons.append("No connected path was found on the sampled grid after excluding land, "
                       "the maritime boundary buffer, Danger areas and vessel-limit violations. "
                       "This does not establish that every possible ocean path is blocked.")
    return " ".join(reasons) + " Do not treat this as permission to sail."


def calculate(start, end, grid, rows, predictions, hours, speed, wave_limit, wind_limit):
    points, water, width, samples, bounds = grid
    points = [*points, tuple(start), tuple(end)]
    water = np.r_[water, True, True]
    sample_risks = [envelope(row, hours, wave_limit, wind_limit, pred) for row, pred in zip(rows, predictions)]
    def local_risk(p):
        # Conservative local envelope from the 3 closest forecast samples.
        nearest = sorted(range(len(samples)), key=lambda i: distance(p, samples[i]))[:3]
        return combine_risks([sample_risks[i] for i in nearest])

    risks = [local_risk(p) for p in points]
    adjacency = [[] for _ in points]
    edge_risks = {}
    n = len(points) - 2

    def connect(i, j):
        if water[i] and water[j] and not risks[i]["blocked"] and not risks[j]["blocked"] and boundary.segment_allowed(points[i], points[j]) and segment_clear(points[i], points[j]):
            # Do not skip blocked intermediate grid cells, even on endpoint connectors.
            traversed = [local_risk(p) for p in np.linspace(points[i], points[j], max(2, math.ceil(distance(points[i], points[j])) + 1))]
            risk = combine_risks(traversed)
            if risk["blocked"]:
                return
            d = distance(points[i], points[j])
            adjacency[i].append((j, d, risk["risk"], risk["hazard_score"]))
            adjacency[j].append((i, d, risk["risk"], risk["hazard_score"]))
            edge_risks[i, j] = edge_risks[j, i] = risk

    for i in range(n):
        r, c = divmod(i, width)
        for dr, dc in [(0, 1), (1, -1), (1, 0), (1, 1)]:
            nr, nc = r + dr, c + dc
            j = nr * width + nc
            if 0 <= nc < width and 0 <= j < n:
                connect(i, j)
    for endpoint in [n, n + 1]:
        closest = sorted((i for i in range(n) if water[i]), key=lambda i: distance(points[endpoint], points[i]))[:12]
        for j in closest:
            connect(endpoint, j)
    paths = []
    shortest = solve_graph(points, adjacency, risks, n, n + 1, 0)
    safest = safest_shortest(points, adjacency, risks, n, n + 1, hours * speed * 1.852)
    for label, path in [("Shortest feasible", shortest), ("Safest then shortest", safest)]:
        if path is None:
            continue
        length = sum(distance(points[a], points[b]) for a, b in zip(path, path[1:]))
        duration = length / (speed * 1.852)
        if duration > hours:
            continue
        path_risks = [edge_risks[a, b] for a, b in zip(path, path[1:])]
        exposure = sum(distance(points[a], points[b]) * edge_risks[a, b]["risk"] ** 2 for a, b in zip(path, path[1:]))
        paths.append({"name": label, "coordinates": [points[i] for i in path], "distance_km": length,
                      "boundary":boundary.assess_path([points[i] for i in path]),
                      "recommended": label == "Safest then shortest", "label": LABELS[max(r["label_id"] for r in path_risks)],
                      "label_origin": "project_derived_from_government_guidance", "peak_hazard_score": max(r["hazard_score"] for r in path_risks),
                      "duration_hours": duration, "exposure_index_km": exposure,
                      "peak_risk_ratio": max(r["risk"] for r in path_risks),
                      "max_wave_m": max(r["wave_m"] for r in path_risks), "max_wind_ms": max(r["wind_ms"] for r in path_risks)})
    shortest_km = sum(distance(points[a], points[b]) for a, b in zip(shortest, shortest[1:])) if shortest else None
    return {"routes": paths, "status": "available" if paths else "no_route", "bounds": bounds,
            "message": "Routes satisfy the selected limits on the sampled planning grid." if paths else no_route_reason(risks[n], risks[n + 1], wave_limit, wind_limit, shortest_km, hours, speed),
            "samples": [{"latitude": p[0], "longitude": p[1], **r} for p, r in zip(samples, sample_risks)],
            "forecast_window_hours": hours, "grid_nodes": len(points),
            "method": "Exclude land, Danger classes and vessel-limit violations. Minimise peak class-plus-limit-ratio hazard within the trip duration, then minimise distance at that hazard. Also display shortest feasible for comparison. Worst forecast over the full trip window; three nearest samples form the local envelope.",
            "limitations": ["Research decision support; not certified navigation.", "Treaty-derived India–Sri Lanka boundary approximation with a project buffer; other legal zones, reefs, depth and shipping lanes are not checked.", "Shortest is on the planning grid within the search area, not a globally shortest ocean route.", "Vessel limits are user inputs, not validated vessel safety ratings."]}
