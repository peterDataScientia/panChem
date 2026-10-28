# panChem

Automated extraction and chemical review of molecular structures from research PDFs.

## Architecture (for maintainers)

- Streamlit Community Cloud is the frontend; use Python 3.11 and app.py.
- GitHub Actions runs DECIMER inside a prebuilt GHCR worker image.
- Uploaded PDFs are committed under jobs/<job-id>/paper.pdf; the GitHub push event triggers .github/workflows/decimer.yml.
- Streamlit needs a fine-grained GITHUB_TOKEN secret with Contents read/write and Actions read. Actions write is not necessary for the push-triggered workflow.
- The worker emits starting, extracting, per-page completion, results_ready and failure events using progress_events.py.
- A Streamlit bidirectional component in components/live_progress/index.html subscribes to a persistent ntfy.sh Server-Sent Events feed. No interval-based GitHub polling is required for live progress.
- When a results_ready event arrives, app.py verifies the associated GitHub run and loads the extraction artifact automatically.
- The PDF viewer in pdf_viewer.py offers page navigation, zoom, fit options and browser fullscreen. chemqa.py handles SMILES QA and 2D SVG depictions.

## Privacy and limitations

**WARNING:** PDFs uploaded via this version are committed to a public GitHub repository and remain accessible until deleted. Upload only PDFs that may legally be published.

Progress notifications go through the public external service ntfy.sh. The topic is based on a random job ID; the event payload includes job/run identifiers, stage names and numeric progress. No PDF bytes, SMILES, extracted structure images, usernames or filenames are sent to ntfy.sh. Do not transmit confidential progress information there.

The progress relay is best-effort. If unavailable, GitHub extraction can still complete; previous completed analyses can be opened from the app.

DECIMER recognition and Markush structures must be verified against original source figures. SMILES syntactic validity does not guarantee chemical fidelity.

## CI checks

- .github/workflows/frontend-smoke.yml: Python syntax, 2D drawings, interactive PDF viewer, SSE component.
- .github/workflows/build-worker.yml: builds and publishes the preinstalled DECIMER Docker image.
- .github/workflows/decimer.yml: actual chemical extraction and artifact output.
- Each new extraction artifact includes `performance.json` with worker startup, model-import, render, segmentation and recognition wall-clock seconds by page; `results.csv` and SMILES outputs are unchanged.
- The extraction workflow now restores the previous model cache as a fallback but writes a new versioned cache **after** inference, when recognition weights are present; failure to save cache does not invalidate successful extraction. First new cache creation may add upload time, later runs can reuse the complete model cache.
- **Serial recognition-model loading is now the default.** On the seven-structure page-2 benchmark, parallel loading increased the worker duration from 29.914 s to 45.002 s while segmentation grew from 8.441 s to 26.648 s. Concurrent model loading is therefore disabled by default on the CPU runner. Use Actions variable `PANCHEM_PARALLEL_MODEL_PRELOAD=1` only for controlled A/B testing (default `0`). Direct RGB-array inference, exact-image prediction caching, 300 DPI rendering, model versions and output formats remain unchanged.
- Recognition consumes the in-memory RGB array used for the original exported PNG, avoiding another PNG read; exact pixel duplicates within a job reuse the same prediction. No fuzzy chemical similarity/deduplication is performed. Each extracted structure still has its own record and image.
- The timing JSON now includes `recognition_preload_seconds`, `recognition_preload_wait_seconds`, per-structure `prediction_timings`, and `recognition_cache_hits`. Overlapped stages are not additive; compare total worker time against the previous 29.914-second baseline from page 2 (7 structures) with matched input and similar runner conditions. A faster result is not yet established.
- The collapsed **Performance diagnostics** expander in Streamlit can display timing from an opened extraction, fetch official GitHub job-step times on demand, compare the two most recent completed worker-image builds and copy the JSON timing report directly from the app without downloading it. Older artifacts may not include per-page timing.

**Keep developer error logs and secrets in GitHub Actions.** The collapsed in-app diagnostics panel exposes only non-sensitive timing and count data, not PDFs, SMILES, filenames or credentials.
