"""Opt-in real network smoke check. Start the backend before running."""
import json
from datetime import datetime, timezone
from pathlib import Path
import time

import httpx


def main():
    records=[]
    with httpx.Client(base_url="http://127.0.0.1:8000",timeout=120) as client:
        for name in ["conditions", "routes"]:
            started=time.perf_counter()
            response=(client.get('/api/conditions?latitude=13.13&longitude=80.34') if name=="conditions" else client.post('/api/routes',json={"start":{"latitude":13.13,"longitude":80.34},"end":{"latitude":13.25,"longitude":80.65}}))
            response.raise_for_status()
            data=response.json()
            record={"endpoint":name,"elapsed_seconds":time.perf_counter()-started,"status_code":response.status_code,"fetched_at":data["fetched_at"]}
            if name=="conditions":
                assert data["prediction"]["available"], data["prediction"]
                record["wave_m"]=data["current"]["wave_m"]
                record["predicted_wave_m"]=data["prediction"]["wave_m"]
                risk=data["prediction"]["risk_prediction"]
                assert risk["available"]
                record["current_risk"]=data["current_risk"]["label"]
                record["predicted_risk"]=risk["label"]
                record["selected_model"]=risk["selected_model"]
            else:
                assert data["status"] in ["available","no_route"]
                record["route_status"]=data["status"]
                record["routes"]=[{k:r[k] for k in ["name","distance_km","exposure_index_km","label","peak_hazard_score","peak_risk_ratio"]} for r in data["routes"]]
            records.append(record)
    report={"checked_at":datetime.now(timezone.utc).isoformat(),"note":"Individual real API smoke measurements, not a benchmark.","results":records}
    Path('artifacts/live-check.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))


if __name__=="__main__":
    main()
