# Research methodology: Random Forest and XGBoost ocean risk prediction

## Research question

How well can Random Forest and XGBoost forecast a government-guidance-derived sea-risk class six hours ahead, and how does minimising peak estimated risk affect route distance compared with shortest feasible routing?

The implementation is reproducible and functional. It does not establish novelty over the literature, government endorsement, accident reduction or proven navigational safety.

## Data and target construction

The supplied ECMWF historical analysis files span 2019–2025 at six-hour intervals over 7–14°N, 77–81°E. The wave grid is 0.5° (15×9), and the atmospheric grid is 0.25° (29×17). Wave fields include significant wave height, mean period and direction. Atmospheric fields include u/v wind, air/sea temperature and pressure; accumulated precipitation is in separate files.

The implemented models use significant wave height and wind magnitude. Period, direction, temperature, pressure and precipitation are not used as predictors. Record the exact original data product and download licence before publication; NetCDF metadata identifies ECMWF and GRIB analysis fields but no safety annotations.

Wind magnitude is `sqrt(u10² + v10²)` in m/s. Select the finer wind grid at the matching wave coordinates. Sort by grid cell and time. Construct current and six-hour-lagged wave/wind features, latitude/longitude and sine/cosine encodings of season and hour. Require exact six-hour lag and lead intervals. Exclude missing or negative physical values; missing sea values never become zeros.

Two targets are generated at `t+6h`:

1. Significant wave height (continuous regression).
2. A project-derived three-class label from the **future** wave and wind values.

Future wave/wind values are target-construction inputs, never model input features. This distinction prevents a misleading near-perfect contemporaneous threshold-reconstruction experiment.

## Government-source provenance and project assumptions

The source is [IMD's Cyclone Warning in India SOP, Table 6.10, printed p.146](https://rsmcnewdelhi.imd.gov.in/images/pdf/sop.pdf#page=174). The table describes sea states, rather than a three-class fishing-safety target. The project aggregates the supplied wave/wind ranges into Safe, Moderate and Danger, taking the worse component. Exact rules and source URLs are versioned in `artifacts/label_policy.json`.

The mapping to safety names, aggregation across wind and waves, continuous wind cutoffs and use on significant combined wave height are project assumptions. No absence-of-warning inference is used to fabricate Safe records. No actual historical IMD/INCOIS warning-label dataset was obtained. “Safe” is a relative class name, not a statement of vessel-specific safety.

The compressed CSV export preserves issue and target timestamps, spatial coordinates, physical values, target class, dataset split, label origin and policy version. Some cross-year rows are retained in the export with `purged_boundary` status but excluded from the validation/test scores.

## Evaluation design

- Train: 2019–2023, **408,912** rows.
- Validate: 2024, **81,928** rows.
- Test: 2025, **81,704** rows.

A row's issue and target timestamps must both lie in its evaluation split. A lagged observation preceding a split is permitted because it exists at issue time; future targets never enter fitting. No random temporal train/test split is used. Adjacent grid cells and times remain correlated, so row count is not an independent-trial count.

Class distribution:

| Split | Safe | Moderate | Danger |
| --- | ---: | ---: | ---: |
| Train | 247,879 | 150,026 | 11,007 |
| Validation | 51,137 | 28,665 | 2,126 |
| Test | 49,431 | 29,693 | 2,580 |

Class imbalance makes accuracy alone insufficient; report macro F1, balanced accuracy, per-class precision/recall and confusion matrices.

## Model configurations

Random Forest regressor: 120 trees, depth ≤18, minimum leaf size 5, feature fraction 0.8. Random Forest classifier: 140 trees, depth ≤18, minimum leaf size 8, feature fraction 0.8 and balanced-subsample class weighting.

XGBoost: 300 trees, depth 6, learning rate 0.05, row subsampling 0.85, feature subsampling 0.9, L2 regularisation 2 and CPU histogram training. The classifier uses multiclass soft probabilities with balanced training sample weights. Both families use seed 42 and four training threads.

Wave model selection uses 2024 MAE; classification selection uses 2024 macro F1, breaking exact ties with Danger recall. Random Forest wins both validation comparisons. Test results are not used to choose the family or tune thresholds. Models are not refitted on validation/test.

These are specified configurations, not an exhaustive hyperparameter search. Scripts and dependency versions are included.

## Results on 2025

| Model | Wave MAE (m) | Wave RMSE (m) | Class accuracy | Balanced accuracy | Macro F1 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Random Forest | 0.07423 | 0.10491 | 0.91643 | 0.87956 | 0.84712 |
| XGBoost | 0.07599 | 0.10732 | 0.90078 | 0.89360 | 0.81100 |
| Persistence | 0.09297 | 0.12986 | 0.90399 | See JSON | 0.84351 |

Persistence carries current wave height/current derived class forward six hours. Random Forest's wave MAE improves by **20.16%** relative to persistence. Its class macro F1 improvement is modest. XGBoost's class macro F1 is below persistence, despite its stronger Danger recall. Do not describe all metrics as improved.

Danger performance:

| Model | Precision | Recall | F1 |
| --- | ---: | ---: | ---: |
| Random Forest | 0.62683 | 0.81512 | 0.70868 |
| XGBoost | 0.48012 | 0.90310 | 0.62693 |

The higher XGBoost recall comes with more false positives. The route guard takes the worse predicted label across both classifiers, favouring exclusion over availability. Its classifier-only conservative combination is separately reported under `conservative_ensemble`; the complete routing system also includes provider forecasts, upper wave bands and vessel limits, so classifier metrics are not route-safety metrics.

Global interpretability uses validation permutation importance (3 repeats, 3,000 rows for classification; 5,000 for regression). Importance describes model sensitivity, not causation. The 90th-percentile absolute validation residual supplies a historical wave band for each regressor. Neither these bands nor the class scores are calibrated safety probabilities; class weighting particularly affects score interpretation.

## Live inference

The marine/weather adapter requests numerical-model estimates for the chosen coordinate, aligns timestamps and retrieves six-hour-lagged inputs. The exact training feature function is reused. The UI shows the current deterministic derived class separately from the learned six-hour class. Auto selects the validation winner for display; both model outputs remain visible.

Operational model estimates differ from historical analyses. Historical accuracy is not prospective live accuracy. Retrieval time and forecast-valid time are recorded separately; retrieval time is not model-run issuance time. No direct live sensor readings are implied.

Incomplete history or missing trip forecast hours block model-backed routing. Offline reloads clear advice. Changing a source, destination, vessel limit or displayed model invalidates existing route results.

## Route objective and algorithm

Build a bounded ocean grid with eight-neighbour edges and offshore endpoint connectors. Apply an approximate land mask and nominal 600 m buffer; sample edges for coastline clearance and weather exposure. Local risk uses the three nearest forecast locations across the entire trip window. Incorporate both custom wave upper bands and both classifier outputs.

Exclude Danger edges, land and vessel-limit violations. For each feasible edge:

`hazard = derived_class_id + max(wave / vessel_wave_limit, wind / vessel_wind_limit)`

Safe=0, Moderate=1. Because feasible ratios are <1, every Moderate edge has a higher hazard than every Safe edge. The optimisation is lexicographic: minimise maximum path hazard, then minimise distance subject to that hazard and the selected trip-duration budget.

Binary-search the sorted edge-hazard thresholds. At each threshold, run shortest-path Dijkstra on permitted edges and check distance against speed × trip hours. Feasibility is monotone as the threshold increases. The lowest feasible threshold determines minimum peak risk; shortest-path Dijkstra at that threshold gives the shortest route at that risk. This avoids a single-label minimax tie-handling error where a longer prefix can hide a shorter final route.

The comparison route minimises distance over all feasible edges. The recommended path may require a substantial detour. Cumulative exposure `Σ(length × ratio²)` is displayed but is not minimised by this objective; it can increase when peak risk decreases. This tradeoff must remain visible to users.

“Safest” only means lowest estimated peak hazard on this sampled graph. No globally optimal ocean route, real-world safety guarantee, depth clearance or maritime legality is established. Current-aware speed, fuel cost and bathymetry are absent.

## Verification and remaining study work

Automated backend tests cover label boundaries, wind escalation, missing values, trained-family selection, probabilities, complete evaluation counts, land/weather exclusions, risk-first optimisation and duration budgets. Browser tests cover desktop/mobile presentation, model changes, stale-response races and offline PWA reloads. Real-network smoke checks separately verify live conditions and route responses; see `unnecessary/historical/artifacts/live-check.json`. Latencies are individual development measurements, not service-level benchmarks.

Still required for publication/operational claims:

- Review related research and justify novelty without inventing literature claims.
- Preserve forecasts as issued and compare them prospectively against later observations/reference analyses.
- Evaluate by season, rare high-wave events, location and distance offshore, using blocked temporal/event-level confidence intervals.
- Measure median/p95 latency and route feasibility, detour and risk tradeoffs across prespecified scenarios.
- Obtain authoritative depth, restricted-area and warning data; validate the treaty-derived boundary against certified charts and vessel-specific thresholds with qualified stakeholders.
- Conduct consenting, supervised onshore usability studies with fishermen and domain reviewers. Measure task success, interpretation errors, completion time and perceived usefulness. No user-study scores have been fabricated.

SDG 14 relevance is intended support for informed small-scale fishing decisions. Ecological benefits, reduced accidents and fuel savings have not been measured.


The interface now separates safety prediction from routing and accepts manually entered coordinates, fetching live model features and history for that location. The route graph excludes the Sri Lankan side of the shared treaty-derived border and a 500 m buffer; segment checks detect exits and re-entry even when both endpoints are on the Indian side. GPS alerts are foreground browser functionality, verified with simulated fixes, not an at-sea operational validation. Boundary proximity includes reported GPS accuracy and the project's map-uncertainty buffer. No claim of certified navigation or background monitoring is made.
