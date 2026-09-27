from datetime import datetime, timezone, timedelta
import math

from fastapi.testclient import TestClient
import numpy as np
import pytest

from backend.main import app
from backend.ocean import DataUnavailable, OceanProvider, provider
from backend.prediction import predictor
from backend.routing import distance, envelope, make_grid, calculate, solve_graph, segment_clear, combine_risks, safest_shortest
from ml.features import features
from ml.risk_policy import label_ids, describe, KNOT_MS, POLICY

client = TestClient(app)


def row(point=(13.13, 80.34), wave=0.7, wind=4):
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    hourly = [{"time": (now+timedelta(hours=i)).isoformat(), "wave_m": wave, "wind_ms": wind,
               "period_s": 6, "wave_direction": 120, "wind_direction": 200} for i in range(-6,50)]
    return {"latitude":point[0], "longitude":point[1], "current":hourly[6], "hourly":hourly,
            "fetched_at":datetime.now(timezone.utc).isoformat(), "source":"TEST FIXTURE", "data_type":"model_estimate"}


def test_feature_shape_and_shared_order():
    X = features(1, .9, 5, 4, 12, 80, 150, 6)
    assert X.shape == (1,10)
    assert list(X[0,:6]) == [1,.9,5,4,12,80]
    assert X[0,8] == pytest.approx(1)


def test_distance():
    assert distance((10,80),(10,80)) == 0
    assert distance((10,80),(11,80)) == pytest.approx(111.195, abs=.01)


def test_land_and_invalid_trip_rejected():
    with pytest.raises(ValueError, match="offshore"):
        make_grid((13.08,80.20),(13.25,80.65))
    with pytest.raises(ValueError, match="1 and 160"):
        make_grid((13.13,80.34),(13.13,80.34))
    assert not segment_clear((10,78.5),(10,80.3))


def test_weather_limits_and_missing_forecasts():
    assert envelope(row(wave=2),12,2,10)["blocked"]
    r=row()
    del r["hourly"][10]
    with pytest.raises(DataUnavailable):
        envelope(r,12,2,10)


def test_prediction_upper_band_used_for_route():
    risk=envelope(row(),12,2,10,{"available":True,"upper_m":2.1})
    assert risk["blocked"]
    assert risk["wave_m"] == 2.1


def test_derived_label_boundaries_and_wind_override():
    assert list(label_ids([1.25,1.2501,2.5,2.5001],[0,0,0,0])) == [0,1,1,2]
    assert list(label_ids([.5,.5,.5],[17*KNOT_MS-1e-6,17*KNOT_MS,22*KNOT_MS])) == [0,1,2]
    assert describe(.5,12)["label"] == "danger"
    assert POLICY["government_issued_labels"] is False
    for wave in [float('nan'),float('inf'),-1]:
        with pytest.raises(ValueError): label_ids(wave,4)


def test_danger_cannot_be_overridden_by_vessel_limits():
    assert envelope(row(wave=3),12,5,25)["blocked"]
    risk=envelope(row(),12,5,25,{"available":True,"upper_m":1,"risk_prediction":{"available":True,"conservative_label_id":2}})
    assert risk["blocked"] and risk["label"]=="danger"


def test_safest_then_shortest_and_trip_budget():
    graph=[[(1,1,.8),(2,1.5,.2)],[(0,1,.8),(3,1,.8)],[(0,1.5,.2),(3,1.5,.2)],[(1,1,.8),(2,1.5,.2)]]
    risks=[{"risk":.1,"blocked":False} for _ in graph]
    assert safest_shortest([],graph,risks,0,3,4)==[0,2,3]
    assert safest_shortest([],graph,risks,0,3,2.5)==[0,1,3]
    assert safest_shortest([],graph,risks,0,3,1.5) is None


def test_peak_risk_tie_uses_globally_shortest_path_on_graph():
    # A low-risk but long prefix must not eliminate a short prefix when a later
    # unavoidable high-risk edge makes their final peak risk identical.
    graph=[[(1,1,.4),(2,5,.1)],[(0,1,.4),(2,5,.1),(3,1,.9)],[(0,5,.1),(1,5,.1)],[(1,1,.9)]]
    risks=[{"risk":.1,"blocked":False} for _ in graph]
    assert safest_shortest([],graph,risks,0,3,20)==[0,1,3]


def test_envelope_preserves_independent_wave_and_wind_peaks():
    combined=combine_risks([
        {"wave_m":1.5,"wind_ms":2,"risk":.75,"blocked":False},
        {"wave_m":.5,"wind_ms":9,"risk":.9,"blocked":False}])
    assert combined["wave_m"]==1.5
    assert combined["wind_ms"]==9
    assert combined["risk"]==.9


def test_shortest_and_lower_exposure_objectives():
    # Short branch through risky node; longer low-exposure alternative.
    graph=[[(1,1),(2,1.5)],[(0,1),(3,1)],[(0,1.5),(3,1.5)],[(1,1),(2,1.5)]]
    risks=[{"risk":v,"blocked":False} for v in [.1,.9,.1,.1]]
    assert solve_graph([],graph,risks,0,3,0)==[0,1,3]
    assert solve_graph([],graph,risks,0,3,5)==[0,2,3]
    risks[1]["blocked"]=True;risks[2]["blocked"]=True
    assert solve_graph([],graph,risks,0,3,5) is None


def test_forecast_adapter_rejects_stale_and_missing_values():
    now=datetime.now(timezone.utc)
    stale=(now-timedelta(hours=5)).replace(tzinfo=None).isoformat()
    marine={"latitude":13,"longitude":80.5,"hourly":{"time":[stale],"wave_height":[.5],"wave_period":[6],"wave_direction":[90]}}
    weather={"hourly":{"time":[stale],"wind_speed_10m":[4],"wind_direction_10m":[90]}}
    with pytest.raises(DataUnavailable):
        OceanProvider.parse(marine,weather,(13,80.5),now)


def test_provider_alignment_uses_timestamps():
    now=datetime.now(timezone.utc).replace(minute=0,second=0,microsecond=0)
    ts=[(now-timedelta(hours=1)).replace(tzinfo=None).isoformat(),now.replace(tzinfo=None).isoformat()]
    marine={"latitude":13,"longitude":80.5,"hourly":{"time":ts,"wave_height":[.5,.6],"wave_period":[6,6],"wave_direction":[90,90]}}
    weather={"hourly":{"time":ts[::-1],"wind_speed_10m":[8,4],"wind_direction_10m":[90,90]}}
    result=OceanProvider.parse(marine,weather,(13,80.5),now)
    assert result["current"]["wind_ms"]==8


def test_prediction_real_artifact():
    result=predictor.predict(row())
    assert result["available"]
    assert 0<=result["lower_m"]<=result["wave_m"]<=result["upper_m"]
    assert datetime.fromisoformat(result["target_time"])-datetime.fromisoformat(result["issue_time"])==timedelta(hours=6)
    r=row();r["hourly"]=r["hourly"][1:]
    assert not predictor.predict(r)["available"]


def test_both_trained_models_predict_live_features():
    for name in ["random_forest","xgboost"]:
        result=predictor.predict(row(),name)
        assert result["selected_model"]==name
        risk=result["risk_prediction"]
        assert risk["available"] and risk["selected_model"]==name
        assert set(risk["models"])=={"random_forest","xgboost"}
        assert sum(risk["class_scores"].values())==pytest.approx(1,abs=1e-5)
        assert risk["label"] in ["safe","moderate","danger"]
        assert result["conservative_upper_m"] >= result["upper_m"]


@pytest.fixture
def fake_live(monkeypatch):
    async def fetch(points): return [row(p) for p in points]
    monkeypatch.setattr(provider,"fetch",fetch)


def test_conditions_api(fake_live):
    result=client.get('/api/conditions?latitude=13.13&longitude=80.34')
    assert result.status_code==200
    assert result.json()["prediction"]["available"]
    assert client.get('/api/conditions?latitude=30&longitude=80').status_code==422
    assert client.get('/api/conditions?latitude=13.08&longitude=80.2').status_code==422
    assert client.get('/api/conditions?latitude=13.13&longitude=80.34&model=invalid').status_code==422
    assert client.get('/api/conditions?latitude=13.13&longitude=80.34&model=xgboost').json()["prediction"]["selected_model"]=="xgboost"


def test_no_silent_fallback(monkeypatch):
    async def unavailable(points): raise DataUnavailable("Offline test")
    monkeypatch.setattr(provider,"fetch",unavailable)
    result=client.get('/api/conditions?latitude=13.13&longitude=80.34')
    assert result.status_code==503
    assert "current" not in result.json()


def test_routes_and_exceeded_limits(fake_live):
    request={"start":{"latitude":13.13,"longitude":80.40},"end":{"latitude":13.18,"longitude":80.52}}
    result=client.post('/api/routes',json=request)
    assert result.status_code==200, result.text
    routes=result.json()["routes"]
    assert len(routes)==2
    assert routes[1]["recommended"]
    assert routes[1]["peak_hazard_score"]<=routes[0]["peak_hazard_score"]+1e-8
    assert routes[0]["distance_km"]<=routes[1]["distance_km"]+1e-8
    for r in routes:
        assert r["coordinates"][0]==[13.13,80.4]
        assert r["coordinates"][-1]==[13.18,80.52]
        assert r["peak_risk_ratio"]<1
        assert all(segment_clear(a,b) for a,b in zip(r["coordinates"],r["coordinates"][1:]))
    request["max_wave_m"]=0.3
    result=client.post('/api/routes',json=request)
    assert result.status_code==200
    assert result.json()["status"]=="no_route"
    assert result.json()["routes"]==[]


def test_app_shell_and_install_manifest():
    assert client.get('/').status_code==200
    manifest=client.get('/manifest.webmanifest').json()
    assert manifest["display"]=="standalone"
    for icon in manifest["icons"]:
        assert client.get(icon["src"]).status_code==200
    assert client.get('/api/evaluation').json()["test_year"]==2025


def test_evaluation_is_derived_and_includes_both_models():
    c=client.get('/api/evaluation').json()["classification"]
    assert c["policy"]["government_issued_labels"] is False
    assert set(c["models"])=={"random_forest","xgboost"}
    for name,m in c["models"].items():
        assert sum(map(sum,m["test"]["confusion_matrix"]))==sum(c["class_counts"]["test"].values())
    assert c["models"][c["selected_classifier"]]["validation"]["macro_f1"] == max(m["validation"]["macro_f1"] for m in c["models"].values())


def test_manual_prediction_without_live_provider(monkeypatch):
    async def forbidden(points): raise AssertionError('Manual prediction must not fetch weather')
    monkeypatch.setattr(provider,'fetch',forbidden)
    payload=dict(latitude=13.13,longitude=80.34,observed_at='2026-09-17T06:00:00Z',wave_m=1.2,wind_ms=6,wave_6h_ago_m=1,wind_6h_ago_ms=5)
    for model in ['random_forest','xgboost']:
        response=client.post('/api/predict/manual',json={**payload,'model':model})
        assert response.status_code==200,response.text
        assert response.json()['prediction']['risk_prediction']['selected_model']==model
        assert response.json()['data_type']=='manual_input'
    for change in [dict(wave_m=-1),dict(observed_at='2026-09-17T06:00:00'),dict(latitude=30)]:
        assert client.post('/api/predict/manual',json={**payload,**change}).status_code==422
    del payload['wave_6h_ago_m']
    assert client.post('/api/predict/manual',json=payload).status_code==422


def test_border_geometry_and_crossing_between_indian_endpoints():
    from backend import boundary
    assert boundary.status(8.5,78.95)['status']=='inside'
    assert boundary.status(8.5,79.15)['status']=='outside'
    assert boundary.status(15,80)['status']=='unknown'
    assert boundary.status(9.1,79.533333333)['status']=='uncertain'
    a,b=(9.3,79.48),(9.9,79.50)
    assert boundary.allowed(a) and boundary.allowed(b)
    assert boundary.intersects(a,b)
    assert not boundary.segment_allowed(a,b)


def test_foreign_destination_blocked_before_weather(monkeypatch):
    async def forbidden(points): raise AssertionError('Blocked route must not fetch weather')
    monkeypatch.setattr(provider,'fetch',forbidden)
    response=client.post('/api/routes',json={'start':{'latitude':8.5,'longitude':78.95},'end':{'latitude':8.5,'longitude':79.15}})
    assert response.status_code==200,response.text
    assert response.json()['status']=='boundary_blocked'
    assert response.json()['routes']==[]
    assert response.json()['boundary']['crosses_boundary']


def test_browser_and_backend_boundary_agree():
    import json, subprocess
    from backend import boundary
    points=[[8.5,78.95,10],[8.5,79.15,10],[9.1,79.533333333,10],[15,80,10],[13.13,80.34,10]]
    script="const b=require('./web/boundary.js'),d=require('./web/data/maritime-boundary.json');console.log(JSON.stringify("+json.dumps(points)+".map(p=>b.assess(d,...p))));"
    reports=json.loads(subprocess.check_output(['node','-e',script],text=True))
    for point,report in zip(points,reports):
        expected=boundary.status(*point)
        assert report['status']==expected['status']
        assert report['side']==expected['side']
        if expected['distance_km'] is not None:
            assert report['distance_km']==pytest.approx(expected['distance_km'],abs=1e-8)
