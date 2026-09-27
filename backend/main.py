import asyncio
from datetime import datetime, timezone, timedelta
import time
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, ConfigDict, AwareDatetime

from backend.ocean import provider, DataUnavailable
from backend.prediction import predictor
from backend.routing import make_grid, calculate, ocean_clear
from ml.risk_policy import POLICY, describe
from backend import boundary

app = FastAPI(title="Kadal • Fishermen Safety & Route Advisory", version="0.1.0")
route_gate = asyncio.Semaphore(2)

PORTS = [
    {"id": "chennai", "name": "Chennai · Kasimedu", "latitude": 13.13, "longitude": 80.34, "destination": [13.25, 80.65]},
    {"id": "cuddalore", "name": "Cuddalore", "latitude": 11.70, "longitude": 79.84, "destination": [11.85, 80.15]},
    {"id": "nagapattinam", "name": "Nagapattinam", "latitude": 10.76, "longitude": 79.91, "destination": [10.90, 80.20]},
    {"id": "thoothukudi", "name": "Thoothukudi", "latitude": 8.75, "longitude": 78.25, "destination": [8.60, 78.55]},
    {"id": "kanyakumari", "name": "Kanyakumari", "latitude": 7.99, "longitude": 77.56, "destination": [7.78, 77.80]},
]


class Point(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    latitude: float = Field(ge=7, le=14)
    longitude: float = Field(ge=77, le=81)

    def pair(self):
        return self.latitude, self.longitude


class RouteRequest(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    start: Point
    end: Point
    speed_knots: float = Field(default=8, ge=2, le=30)
    max_wave_m: float = Field(default=2, ge=0.3, le=5)
    max_wind_ms: float = Field(default=10, ge=2, le=25)
    trip_hours: int = Field(default=12, ge=6, le=24)
    model: Literal["auto", "random_forest", "xgboost"] = "auto"


class ManualPredictionRequest(Point):
    observed_at: AwareDatetime
    wave_m: float = Field(ge=0, le=20)
    wind_ms: float = Field(ge=0, le=80)
    wave_6h_ago_m: float = Field(ge=0, le=20)
    wind_6h_ago_ms: float = Field(ge=0, le=80)
    model: Literal["auto", "random_forest", "xgboost"] = "auto"


@app.post('/api/predict/manual')
async def manual_prediction(req: ManualPredictionRequest):
    started=time.perf_counter()
    if not ocean_clear([req.pair()])[0]:
        raise HTTPException(422, 'Select an offshore location for the ocean model.')
    issued=req.observed_at.astimezone(timezone.utc)
    current={'time':issued.isoformat(),'wave_m':req.wave_m,'wind_ms':req.wind_ms}
    lag={'time':(issued-timedelta(hours=6)).isoformat(),'wave_m':req.wave_6h_ago_m,'wind_ms':req.wind_6h_ago_ms}
    row={'latitude':req.latitude,'longitude':req.longitude,'current':current,'hourly':[lag,current]}
    result=await asyncio.to_thread(predictor.predict,row,req.model)
    if not result.get('available') or not result.get('risk_prediction',{}).get('available'):
        raise HTTPException(503, 'Trained models unavailable. Manual prediction cannot be produced.')
    return {**row,'data_type':'manual_input','source':'User-entered scenario; not live observations',
            'prediction':result,'current_risk':describe(req.wave_m,req.wind_ms),
            'response_ms':round((time.perf_counter()-started)*1000,1)}


@app.get("/api/health")
async def health():
    folder = Path(__file__).resolve().parents[1] / "artifacts"
    return {"status": "ok", "model_ready": (folder / "wave_model.joblib").exists(), "classifiers_ready": (folder / "risk_model.joblib").exists()}


@app.get("/api/label-policy")
async def label_policy():
    return POLICY


@app.get("/api/labeled-dataset")
async def labeled_dataset():
    path = Path(__file__).resolve().parents[1] / "artifacts/labeled_dataset.csv.gz"
    if not path.exists():
        raise HTTPException(404, "Run python -m ml.train_classifiers to export the derived-label dataset.")
    return FileResponse(path, media_type="application/gzip", filename="kadal-project-derived-labels.csv.gz")


@app.get("/api/config")
async def config():
    return {"ports": PORTS, "bounds": [[7, 77], [14, 81]], "attribution": "Weather and marine forecasts: Open-Meteo and its model providers, including DWD. Map: © OpenStreetMap contributors.",
            "port_note": "Presets are offshore staging points near each port, not harbour navigation routes."}


@app.get("/api/evaluation")
async def evaluation():
    return predictor.report()


@app.get("/api/conditions")
async def conditions(latitude: float = Query(ge=7, le=14), longitude: float = Query(ge=77, le=81), model: Literal["auto", "random_forest", "xgboost"] = "auto"):
    t0 = time.perf_counter()
    if not ocean_clear([(latitude, longitude)])[0]:
        raise HTTPException(422, "Select an offshore location away from mapped land.")
    try:
        row = (await provider.fetch([(latitude, longitude)]))[0]
        prediction = await asyncio.to_thread(predictor.predict, row, model)
    except DataUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    return {**row, "prediction": prediction, "current_risk": describe(row["current"]["wave_m"], row["current"]["wind_ms"]), "response_ms": round((time.perf_counter() - t0) * 1000, 1)}


@app.post("/api/routes")
async def routes(req: RouteRequest):
    t0 = time.perf_counter()
    direct_boundary=boundary.assess_path([req.start.pair(),req.end.pair()])
    if not boundary.allowed(req.start.pair()) or not boundary.allowed(req.end.pair()):
        return {'routes':[],'status':'boundary_blocked','boundary':direct_boundary,
                'message':'Border warning: source or destination is beyond, or too close to, the mapped maritime boundary. Select offshore points on the Indian side, clear of the 500 m buffer.',
                'response_ms':round((time.perf_counter()-t0)*1000,1)}
    async with route_gate:
        try:
            grid = await asyncio.to_thread(make_grid, req.start.pair(), req.end.pair())
            rows = await provider.fetch(grid[3])
            predictions = await asyncio.to_thread(lambda: [predictor.predict(row, req.model) for row in rows])
            if not all(p.get("available") and p.get("risk_prediction", {}).get("available") for p in predictions):
                raise DataUnavailable("Trained Random Forest/XGBoost predictions or their input history are unavailable. Route advice is paused.")
            result = await asyncio.to_thread(calculate, req.start.pair(), req.end.pair(), grid, rows, predictions, req.trip_hours, req.speed_knots, req.max_wave_m, req.max_wind_ms)
        except DataUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    return {**result, "fetched_at": min(row["fetched_at"] for row in rows), "generated_at": datetime.now(timezone.utc).isoformat(),
            "boundary":direct_boundary,
            "ml_used": all(p.get("available", False) for p in predictions), "source": rows[0]["source"],
            "response_ms": round((time.perf_counter() - t0) * 1000, 1), "settings": req.model_dump()}


app.mount("/", StaticFiles(directory=Path(__file__).resolve().parents[1] / "web", html=True), name="mobile")
