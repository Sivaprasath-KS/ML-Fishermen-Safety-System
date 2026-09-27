"""Identical six-hour features for historical training and live inference."""
import numpy as np

FEATURES = ["wave_now_m", "wave_6h_ago_m", "wind_now_ms", "wind_6h_ago_ms",
            "latitude", "longitude", "season_sin", "season_cos", "hour_sin", "hour_cos"]


def features(wave, lag_wave, wind, lag_wind, lat, lon, day, hour):
    return np.column_stack(np.broadcast_arrays(
        wave, lag_wave, wind, lag_wind, lat, lon,
        np.sin(2 * np.pi * np.asarray(day) / 365.25),
        np.cos(2 * np.pi * np.asarray(day) / 365.25),
        np.sin(2 * np.pi * np.asarray(hour) / 24),
        np.cos(2 * np.pi * np.asarray(hour) / 24),
    ))
