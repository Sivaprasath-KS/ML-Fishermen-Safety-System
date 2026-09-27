"""Project-derived three-class policy; never government-issued warning labels."""
import numpy as np

LABELS = ["safe", "moderate", "danger"]
KNOT_MS = 1852 / 3600
POLICY = {
    "version": "imd-sea-state-derived-v1",
    "label_origin": "project_derived_from_government_guidance",
    "government_issued_labels": False,
    "source_title": "IMD Cyclone Warning in India — Standard Operation Procedure, Table 6.10, printed page 146",
    "source_url": "https://rsmcnewdelhi.imd.gov.in/images/pdf/sop.pdf#page=174",
    "boundary_source_url": "https://www.nodc.noaa.gov/gtspp/document/codetbls/wmocodes/table3700.html",
    "mapping": {
        "safe": "Wave height ≤1.25 m AND wind <17 knots (8.746 m/s).",
        "moderate": "Wave height >1.25 to 2.5 m OR wind ≥17 to <22 knots, unless danger applies.",
        "danger": "Wave height >2.5 m OR wind ≥22 knots (11.318 m/s).",
    },
    "wave_boundaries_m": [1.25, 2.5], "wind_boundaries_knots": [17, 22],
    "boundary_rule": "Exact wave boundaries retain the lower sea-state group (WMO 3700). Wind uses continuous cutoffs at the published lower knot bounds.",
    "aggregation": "The worse of wave and wind groups determines the project class.",
    "meaning": "Safe means the lowest project weather-risk class, not permission to sail or a vessel-specific guarantee.",
    "limitations": "IMD publishes sea-state descriptions, not this three-class safety taxonomy. This mapping, its wind/wave combination and its use on significant total wave height are project assumptions, not government endorsements. No historical official warning labels are present.",
}


def label_ids(wave_m, wind_ms):
    wave, wind = np.broadcast_arrays(np.asarray(wave_m, dtype=float), np.asarray(wind_ms, dtype=float))
    if not np.isfinite(wave).all() or not np.isfinite(wind).all() or (wave < 0).any() or (wind < 0).any():
        raise ValueError("Risk labels require finite, nonnegative wave and wind values.")
    waves = np.where(wave > 2.5, 2, np.where(wave > 1.25, 1, 0))
    winds = np.where(wind >= 22 * KNOT_MS, 2, np.where(wind >= 17 * KNOT_MS, 1, 0))
    return np.maximum(waves, winds).astype(np.int32)


def describe(wave_m, wind_ms):
    index = int(label_ids(wave_m, wind_ms))
    wave_id = int(label_ids(wave_m, 0))
    wind_id = int(label_ids(0, wind_ms))
    return {"label": LABELS[index], "label_id": index, "wave_label": LABELS[wave_id], "wind_label": LABELS[wind_id],
            "basis": POLICY["label_origin"], "policy_version": POLICY["version"],
            "explanation": f"Wave height {wave_m:.2f} m → {LABELS[wave_id]}; wind {wind_ms / KNOT_MS:.1f} knots → {LABELS[wind_id]}. The worse group determines the class."}
