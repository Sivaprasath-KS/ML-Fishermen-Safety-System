from backend.routing import no_route_reason


CLEAR = {"blocked": False, "wave_m": 0.5, "wind_ms": 3, "label_id": 0}


def test_endpoint_reports_all_constraints_including_equality():
    blocked = {"blocked": True, "wave_m": 2, "wind_ms": 10, "label_id": 2}
    message = no_route_reason(blocked, CLEAR, 2, 10, None, 12, 8)
    assert "Departure area is excluded" in message
    assert "wave envelope 2.00 m" in message
    assert "wind envelope 10.00 m/s" in message
    assert "Danger" in message
    assert "Destination area is excluded" not in message


def test_model_danger_below_vessel_limits_is_explained():
    blocked = {**CLEAR, "blocked": True, "label_id": 2}
    message = no_route_reason(CLEAR, blocked, 2, 10, None, 12, 8)
    assert "Destination area is excluded" in message
    assert "classification is Danger" in message
    assert "meets or exceeds" not in message


def test_trip_budget_reports_required_time():
    message = no_route_reason(CLEAR, CLEAR, 2, 10, 100, 6, 8)
    assert "100.0 km (6.7 h at 8 knots)" in message
    assert "beyond the selected 6 h" in message


def test_disconnected_grid_does_not_claim_all_ocean_paths_blocked():
    message = no_route_reason(CLEAR, CLEAR, 2, 10, None, 12, 8)
    assert "No connected path" in message
    assert "does not establish" in message
