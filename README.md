# Kadal — Tamil Nadu ocean risk and route advisory

An installable mobile web app using **Random Forest and XGBoost** to predict wave height and **Safe / Moderate / Danger** six hours ahead. The risk labels are **project-derived from official IMD sea-state guidance**, not government-issued warning records.

## Project layout

| Folder | Contents |
| --- | --- |
| `backend/` | API, ocean data, predictions, boundaries, and routing |
| `web/` | Web interface, offline support, icons, and bundled map library |
| `ml/` | Model training, features, and risk policy |
| `DataSet/` | Original historical datasets, grouped by year |
| `artifacts/` | Active trained models, labelled dataset, policy, and evaluation reports |
| `tests/` | Backend and browser tests |
| `scripts/` | Startup, boundary generation, and live checks |
| `deploy/` | Deployment proxy configuration |
| `docs/` | [Project report](docs/PROJECT_REPORT.md) and [research methodology](docs/RESEARCH.md) |
| `unnecessary/` | Archived caches and historical outputs, with [restoration instructions](unnecessary/README.md) |

Dependency manifests, Docker settings, and test configuration stay at the root for their tools. `.venv/` and `node_modules/` contain installed dependencies used by startup and tests. Paths in the documentation are relative to the project root unless stated otherwise. Running tests or live checks can create fresh output files in their normal locations.

## Open the app

```bash
bash scripts/run.sh
```

Open **http://localhost:8000**. Interactive API documentation: **http://localhost:8000/docs**.

Choose an offshore start coordinate or GPS location, a destination, and a prediction model. The app displays live conditions, the current derived class, both models' six-hour forecasts, and two route alternatives.

## Train from scratch

The original `DataSet/2019` through `DataSet/2025` files are preserved.

```bash
python3.14 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m ml.train
.venv/bin/python -m ml.train_classifiers
bash scripts/run.sh
```

The first training command fits both wave regressors; the second fits both risk classifiers and exports labelled data. Restart the server after retraining so inference loads the new models. Training was measured in minutes, not seconds, on this development machine. Both Random Forest and XGBoost CPU implementations are pinned in `requirements.txt`.

## Label provenance

Source: [IMD, Cyclone Warning in India, Table 6.10, printed page 146](https://rsmcnewdelhi.imd.gov.in/images/pdf/sop.pdf#page=174). The table supplies sea-state descriptions and associated wave/wind ranges. The project's three-class mapping is:

| Project label | Rule |
| --- | --- |
| Safe | Wave ≤1.25 m **and** wind <17 knots |
| Moderate | Wave >1.25 to 2.5 m **or** wind ≥17 to <22 knots, unless Danger applies |
| Danger | Wave >2.5 m **or** wind ≥22 knots |

The worse wave/wind group wins. Exactly 1.25 and 2.5 m remain in the lower wave group, consistent with the [WMO 3700 boundary convention hosted by NOAA](https://www.nodc.noaa.gov/gtspp/document/codetbls/wmocodes/table3700.html). Wind uses the documented continuous lower-knot cutoffs; conversion is 1 knot = 1852/3600 m/s.

**These class names, aggregation rules and safety interpretation belong to this project.** IMD has not issued these labels for the historical rows. “Safe” only denotes the lowest project weather-risk class; it does not establish a vessel's safety or permission to sail. The NetCDF metadata identifies ECMWF historical analysis data, not annotated warning outcomes.

Generated files:

- `artifacts/labeled_dataset.csv.gz`: issue/target times, coordinates, current/lag/future values, future class, split and provenance.
- `artifacts/label_policy.json`: exact policy, boundaries and source references.
- `artifacts/classification_evaluation.json`: both classifiers, per-class metrics and confusion matrices.
- `artifacts/evaluation.json`: both wave regressors, baseline and historical residual band.
- `artifacts/wave_model.joblib` and `artifacts/risk_model.joblib`: trained models.

Download the labelled dataset from the app's **Model & evidence** tab or `/api/labeled-dataset`.

## Measured model results

Train: **2019–2023**. Validation: **2024**. Held-out test: **2025**. Models use current and six-hour-lagged wave/wind values, location, season and hour features to forecast the class at **t+6h**. They are not evaluated by simply recreating the current label from the same current values.

| Model | Wave test MAE | Class test accuracy | Class macro F1 | Danger recall |
| --- | ---: | ---: | ---: | ---: |
| Random Forest | 0.07423 m | 91.64% | 0.8471 | 81.51% |
| XGBoost | 0.07599 m | 90.08% | 0.8110 | 90.31% |
| Persistence | 0.09297 m | 90.40% | 0.8435 | See evaluation JSON |

Random Forest is selected automatically for both tasks using **validation** MAE/macro F1. XGBoost has higher Danger recall but more false alarms and lower macro F1. The app exposes both models instead of concealing this tradeoff. Class scores are uncalibrated model outputs, not accident probabilities. Read [Research methodology](docs/RESEARCH.md) for full methodology.

## Live location predictions

Open-Meteo marine and weather APIs provide current-hour numerical-model estimates, six hours of history and future conditions at requested ocean coordinates. Wave and wind series are aligned by timestamp. Both trained model families evaluate the same live features; Auto chooses the validation winner for display. You can explicitly choose Random Forest or XGBoost.

Routing always uses conservative checks across **both** models, irrespective of the display selection. API errors, missing history or incomplete trip forecasts do not produce invented calm conditions. No direct buoy or onboard-sensor stream is integrated; live operational-model inputs differ from the historical training analyses.

Study coverage: **7–14°N, 77–81°E**. Endpoint separation: **1–160 km**. Presets near Chennai, Cuddalore, Nagapattinam, Thoothukudi and Kanyakumari are offshore staging points, not harbour exits.

## Safest, then shortest routing

1. Build a local ocean grid with up to 27 forecast sample points.
2. Form conservative wave/wind envelopes across the entire trip window using each location's three nearest samples.
3. Include both regressors' upper historical wave bands and both classifiers' six-hour labels.
4. Exclude mapped land, coastline-buffer violations, **Danger** predictions and vessel-limit violations. Raising vessel limits never permits Danger cells.
5. Minimise the highest hazard score on the path, then choose the **shortest path at that hazard level** within the trip duration.

For feasible edges, `hazard = class_id + max(wave / vessel_wave_limit, wind / vessel_wind_limit)`, where Safe=0 and Moderate=1. Since feasible limit ratios are below 1, class priority is preserved. Binary-searching hazard thresholds with Dijkstra finds the smallest feasible threshold; Dijkstra then provides the shortest path under it. A separate **Shortest feasible** route shows the distance-first comparison.

This optimises peak risk, not cumulative exposure. The recommended route can be longer and have a higher cumulative exposure index while reducing peak risk. The displayed exposure index is descriptive, not a safety probability. Routes are optimal only on the bounded planning graph, not globally across the ocean. Duration assumes constant boat speed and excludes currents.

## Phone installation and deployment

This implementation is a **PWA**, not an APK or App Store binary. Deploy it at an **HTTPS** address to install on phones:

- Android Chrome: **Install app** / **Add to Home screen**.
- iPhone Safari: **Share → Add to Home Screen**, enabling **Open as Web App** when offered.

[Apple instructions](https://support.apple.com/guide/iphone/iphea86e5236/ios) · [Google instructions](https://support.google.com/chrome/answer/9658361?co=GENIE.Platform%3DAndroid&hl=en)

```bash
# Train first: Docker copies the generated models.
docker build -t kadal .
docker run --rm -p 8000:8000 kadal
```

Use managed HTTPS or the example `deploy/Caddyfile` with `KADAL_DOMAIN` set to a domain you control. Public hosting is not provisioned. Start with one worker and several GB of memory: the land mask and Random Forest models are sizeable. Add authentication/rate limiting and an appropriate API plan before broad public use. Docker configuration is supplied but the image build has not been tested here.

At sea, current forecasts need cellular or satellite internet. Offline mode loads the interface but clears forecasts and disables advice. No emergency communication function is included. Physical Android/iPhone installation has not been tested.

## Verification

```bash
.venv/bin/python -m pytest -q
npm ci
# With the backend running:
npm run test:browser
.venv/bin/python -m scripts.live_check
```

Browser tests use desktop Chrome and an iPhone-sized Chromium viewport, with explicit forecast fixtures. They do not substitute for Safari hardware tests. Browser configuration uses `/usr/bin/google-chrome`; adjust that path if necessary. Real network checks are separately recorded in `unnecessary/historical/artifacts/live-check.json` and consume provider quota.

## Operational limits

This is a research decision-support implementation. The approximate land mask and nominal coastal buffer cannot resolve all islands, reefs or harbour hazards. The India–Sri Lanka maritime boundary is checked using a treaty-derived approximation with a 500 m planning exclusion buffer. Depth, shipping lanes, other restricted fishing zones and automatic official-warning ingestion are not implemented. Wind gusts, wave steepness, currents, vessel stability and accident outcomes are not prediction targets. Official warnings and navigational charts remain necessary.

Labels, retrospective metrics and a route optimiser do not validate real-world safety. Prospective forecast evaluation and stakeholder studies are still required.

## Sources

- User-provided ECMWF NetCDF analysis files; preserve the original download request and licence for publication.
- [IMD sea-state guidance](https://rsmcnewdelhi.imd.gov.in/images/pdf/sop.pdf#page=174), used for the documented project mapping.
- [Open-Meteo Marine API](https://open-meteo.com/en/docs/marine-weather-api) and [Weather API](https://open-meteo.com/en/docs); attribution to Open-Meteo and model providers including DWD.
- [OpenStreetMap](https://www.openstreetmap.org/copyright), [Leaflet](https://leafletjs.com/) and [global-land-mask](https://pypi.org/project/global-land-mask/). Leaflet is bundled with its licence; map tiles are not cached offline.


## Safety prediction and route advisory

The two main sections have independent inputs. In Safety Prediction, enter latitude and longitude manually or use GPS, then select “Fetch live data & predict”. The app fetches live offshore sea data and the required history for that location and runs the selected trained model. Wave and wind measurements are fetched automatically; users do not need to enter them.

Route Advisory compares the shortest feasible path with the lowest peak weather-risk path, using distance to break ties. Both exclude the mapped Sri Lankan side and a minimum 500 m boundary buffer. An invalid destination produces a warning without a route. A dashed red direct-path preview is a warning illustration, not navigation advice.

The shared boundary dataset is `web/data/maritime-boundary.json`, reproducible with `python -m scripts.build_boundary`. Its source vertices come from the 1974 and 1976 India–Sri Lanka agreements; great-circle arcs are densified to at most 500 m. Source links and limitations are embedded in that file. This is an approximate research layer, not a certified nautical chart or determination of fishing rights. Border-monitor coverage is 7–14°N, 77–81°E; the coverage rectangle is not a national boundary.

“Start live GPS” enables on-device checks against the cached boundary. The app displays approaching-border, uncertain, foreign-side and crossing warnings, with optional sound/vibration where supported. Accuracy over 1 km, fixes older than 30 seconds, permission errors and leaving coverage produce unknown status. Crossing detection uses successive fixes and cannot reconstruct an unobserved track. Keep the app open and the screen active: this PWA does not guarantee background or locked-screen GPS. Offline boundary checks remain available after the shell is cached; fresh forecasts/routes require internet. GPS requires HTTPS or localhost.
