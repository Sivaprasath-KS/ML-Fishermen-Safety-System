# Render deployment

The existing FastAPI entry point is `backend.main:app`. Native Python is sufficient; the existing Dockerfile remains an alternative. Python is pinned in `.python-version` to the locally verified 3.14.6. No application secrets or provider keys are required by the current code.

## Service configuration

`render.yaml` defines `kadal-fishermen-safety`, branch `main`, Python runtime, Singapore region, automatic deployment on commits, and `/api/health` health checks.

- Build: `pip install -r requirements.txt`
- Start: `uvicorn backend.main:app --host 0.0.0.0 --port $PORT`
- One Uvicorn worker; Render supplies `PORT`.
- Proposed plan: `2c-4g` (4 GB RAM), **paid and not yet approved**. Do not create this service until the owner approves its price.

Loading the existing application and both joblib bundles peaked at 1,967,200 KiB (about 1.88 GiB) locally. This exceeds Render Free's 512 MB and leaves too little headroom on 2 GB for request concurrency. The proposed 4 GB plan costs $85/month at the time of preparation; confirm the dashboard price before creation. No changes to model files or algorithms are proposed to fit a smaller plan.

References: [Render plans](https://render.com/docs/compute-plans), [pricing](https://render.com/pricing), [Python versions](https://render.com/docs/python-version), [CLI login](https://render.com/docs/cli).

## Repository and model files

The original folder had no Git history or remote. A local `main` branch was initialized. The owner must supply the actual GitHub repository URL and authenticate through GitHub's browser flow. Never put tokens in source files or chat.

Both `artifacts/*.joblib` files are tracked with Git LFS. The wave bundle is 139,878,555 bytes, exceeding GitHub's ordinary 100 MiB file limit. The risk bundle is 60,038,074 bytes. Keep both files, `artifacts/labeled_dataset.csv.gz`, evaluation JSON, label policy, `web/data/maritime-boundary.json`, icons, and vendor files. Original `DataSet/` training data is retained. Archived `unnecessary/` content stays local and is ignored.

Before pushing: `git lfs install --local`, `git lfs ls-files`, and `git lfs fsck`. Verify the Render checkout contains complete model files rather than LFS pointers. A healthy `/api/health` response alone does not load models; a successful conditions prediction is also required.

Browser authentication commands after installing the official CLIs:

```bash
~/.local/bin/gh auth login --hostname github.com --git-protocol https --web
~/.local/bin/gh auth setup-git
~/.local/bin/render login
```

Connect the chosen repository through Render's Git provider settings. After account authentication, validate with `render blueprints validate render.yaml`. Do not guess a repository URL or a production URL.

## Local verification recorded on 27 September 2026

- Backend: 28 passed, two dependency deprecation warnings.
- `npm ci`: passed, zero reported vulnerabilities.
- Browser: 20 passed, four failed. Failures in `tests/browser/app.spec.js` expect removed `#science` and `#guide` sections on desktop/mobile. No UI was restored or behavior changed to hide these failures.
- Existing live smoke: conditions and routes returned HTTP 200; both route alternatives were available. Result stored locally at `artifacts/live-check.json` (ignored).
- Clean dependency resolution: all exact pins resolved with `pip install --dry-run --ignore-installed -r requirements.txt`.
- Local PWA tests passed on desktop/mobile, including offline reload and excluding API responses from the cache.

These are local results, not production or physical-device verification.

## Production acceptance

Once Render reports the deploy is live, record its actual HTTPS URL and deployment ID. Inspect build/runtime logs, then verify `/`, `/api/health`, `/api/config`, `/api/evaluation`, `/manifest.webmanifest`, `/sw.js`, both PNG icons, and the boundary JSON. Check health reports both model files ready.

Use one offshore point (13.13, 80.34) to exercise `/api/conditions` with `model=auto`, `random_forest`, and `xgboost`; shared provider caching avoids redundant upstream requests. Confirm current values, available prediction, and risk classification. Run one route comparison to 13.25, 80.65; an explained `no_route` may be a legitimate weather result. Inspect browser console, service worker registration, mobile overflow, and offline shell with live API data cleared. Never describe a started deployment as successful until these checks finish.

## Phone checks after HTTPS deployment

Android Chrome: open the verified HTTPS URL, then menu → Install app / Add to Home screen.
iPhone Safari: open the verified HTTPS URL, then Share → Add to Home Screen → Add.

On each physical phone:

- Enter offshore latitude/longitude and Fetch & Predict; test Auto, Random Forest, and XGBoost.
- Check current wave, wind and risk; predicted wave, wind and risk; verify missing/error states have no invented values.
- Allow Use My Location; an inland location may correctly be rejected. Also test denied GPS permission.
- Set route source/destination, compare routes, inspect map and border restrictions.
- Launch from the installed icon, reload, rotate, and check mobile layout.
- Disconnect networking and reload: shell should load but live advice should clear. Reconnect and refresh.

Physical installation, GPS accuracy and phone behavior remain unverified until performed on actual devices. This is a research prototype: model estimates, not direct sensors, certified navigation, guaranteed safety, background GPS or emergency communication.
