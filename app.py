import base64
import csv
import hashlib
import io
import json
from pathlib import Path
import time
import uuid
import zipfile
from urllib.parse import urlparse

import pypdfium2 as pdfium
import requests
import streamlit as st
import streamlit.components.v1 as components
from PIL import Image
from chemqa import assess, molecule_svg, rows_from_artifact, make_review_zip
from pdf_viewer import full_page_viewer
from time_utils import format_eat
from performance_diagnostics import performance_from_artifact, github_timing_report
from ketcher_review import validated_ketcher_result, initial_molfile, result_molfile

_live_events = components.declare_component(
    "panchem_live_events", path=str(Path(__file__).parent / "components" / "live_progress")
)

_ketcher_dist = Path(__file__).parent / "components" / "ketcher" / "dist"
_ketcher = (components.declare_component("panchem_ketcher", path=str(_ketcher_dist))
            if (_ketcher_dist / "index.html").is_file() else None)

REPO = "peterDataScientia/panChem"
API = f"https://api.github.com/repos/{REPO}"
WORKFLOW = "decimer.yml"
st.set_page_config(page_title="panChem | DECIMER", page_icon="🧪", layout="wide")
st.title("🧪 panChem")
st.caption("Extract chemical structures from research PDFs.")

def github_token():
    try:
        return st.secrets.get("GITHUB_TOKEN", "")
    except Exception:
        return ""

def api_request(method, url, token, **kwargs):
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28"}
    response = requests.request(method, url, headers=headers, timeout=90, **kwargs)
    if not response.ok:
        raise RuntimeError(f"GitHub API {response.status_code}: {response.text[:500]} | Required permissions: {response.headers.get('X-Accepted-GitHub-Permissions', 'not reported')}")
    return response

def pages_list(spec, count):
    values = set()
    for chunk in spec.replace(" ", "").split(","):
        if not chunk:
            continue
        if "-" in chunk:
            parts = chunk.split("-")
            if len(parts) != 2 or any(not x.isdigit() for x in parts):
                raise ValueError(f"Invalid range: {chunk}")
            first, last = map(int, parts)
            if first > last: raise ValueError("Ranges must ascend")
            values.update(range(first, last + 1))
        elif chunk.isdigit():
            values.add(int(chunk))
        else:
            raise ValueError(f"Invalid page: {chunk}")
    if not values or min(values) < 1 or max(values) > count:
        raise ValueError(f"Choose pages between 1 and {count}")
    return sorted(values)

@st.cache_data(show_spinner=False, max_entries=20)
def preview(data, page):
    doc = pdfium.PdfDocument(data)
    try:
        image = doc[page-1].render(scale=1.5).to_pil().convert("RGB")
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        return stream.getvalue()
    finally:
        doc.close()

token = github_token()
if not token:
    st.error("Extraction is temporarily unavailable. Contact the app administrator.")

uploaded = st.file_uploader("1. Upload PDF", type="pdf")
st.caption("Privacy: Uploaded PDFs are stored publicly. Only upload files you are allowed to share.")
if uploaded:
    data = uploaded.getvalue()
    if len(data) > 25 * 1024 * 1024:
        st.error("Maximum PDF upload size is 25 MB.")
        st.stop()
    try:
        doc = pdfium.PdfDocument(data)
        count = len(doc)
        doc.close()
    except Exception as e:
        st.error(f"Invalid PDF: {e}")
        st.stop()
    st.subheader("2. PDF preview")
    # Interactive page controls live in the viewer, including when fullscreen.
    components.html(
        full_page_viewer(data),
        height=760,
        scrolling=False,
    )

    selected = st.text_input(
        "3. Pages to analyze", value="1",
        help="For example: 1,3,5-8.",
    )
    try:
        pages = pages_list(selected, count)
        st.caption(f"{len(pages)} pages selected")
    except ValueError as e:
        st.error(str(e))
        pages = []
    if st.button("4. Extract structures", type="primary", disabled=not token or not pages):
        job_id = uuid.uuid4().hex[:12]
        pdf_path = f"jobs/{job_id}/paper.pdf"
        page_spec = ",".join(map(str, pages))
        try:
            with st.spinner("Starting extraction…"):
                api_request("PUT", f"{API}/contents/{pdf_path}", token,
                    json={"message": f"panChem PDF job {job_id} pages={page_spec}", "content": base64.b64encode(data).decode("ascii")})
            st.session_state["last_job_id"] = job_id
            st.session_state.pop("panchem_live_state", None)
            st.session_state.pop("panchem_last_event", None)
            st.session_state.pop("panchem_live_error", None)
            st.session_state.pop("manual_view_run_id", None)
            st.session_state.pop("active_artifact_id", None)
            st.session_state.pop("active_artifact_name", None)
            st.session_state.pop("active_run_id", None)
            st.success("Extraction started.")
        except Exception:
            import logging
            logging.exception("PDF submission failed")
            st.error("Could not start extraction. Please try again.")

@st.fragment
def live_progress():
    """SSE push updates; no timer and no GitHub polling."""
    st.subheader("Extraction progress")
    wanted = st.session_state.get("last_job_id", "")
    if not token:
        st.info("Extraction is temporarily unavailable.")
        return
    if not wanted:
        st.caption("Upload a PDF to start.")
        return

    topic = f"panchem-{wanted}"
    event = _live_events(topic=topic, key=f"push-{wanted}", default=None)
    if isinstance(event, dict) and event.get("topic") == topic:
        unique = event.get("id")
        marker = f"{wanted}:{unique}"
        if unique and st.session_state.get("panchem_last_event") != marker:
            st.session_state["panchem_last_event"] = marker
            st.session_state["panchem_live_state"] = dict(event)
            if event.get("stage") == "results_ready":
                run_id = int(event.get("run_id") or 0)
                if run_id:
                    try:
                        # Authenticate and match the GitHub run to the submitted job.
                        run = api_request(
                            "GET", f"{API}/actions/runs/{run_id}", token,
                        ).json()
                        msg = (run.get("head_commit") or {}).get("message", "")
                        if f"panChem PDF job {wanted} " not in msg:
                            raise ValueError("The completion event does not match the submitted job.")
                        artifacts = api_request(
                            "GET", f"{API}/actions/runs/{run_id}/artifacts", token,
                        ).json().get("artifacts", [])
                        available = [
                            a for a in artifacts
                            if not a.get("expired") and
                            a.get("name", "").startswith("panChem-extraction-")
                        ]
                        if not available:
                            raise ValueError("GitHub has not published the result archive yet.")
                        art = available[0]
                        aid = art["id"]
                        if f"artifact-{aid}" not in st.session_state:
                            st.session_state[f"artifact-{aid}"] = api_request(
                                "GET", f"{API}/actions/artifacts/{aid}/zip", token,
                            ).content
                        if st.session_state.get("manual_view_run_id") is None:
                            st.session_state["active_artifact_id"] = aid
                            st.session_state["active_artifact_name"] = art["name"]
                            st.session_state["active_run_id"] = run_id
                        st.session_state.pop("panchem_live_error", None)
                        st.rerun(scope="app")
                    except Exception as exc:
                        st.session_state["panchem_live_error"] = str(exc)

    state = st.session_state.get("panchem_live_state", {})
    if state.get("topic") != topic:
        st.info("Preparing your document…")
        return
    stage = state.get("stage")
    pages = int(state.get("total_pages") or 0)
    completed = int(state.get("completed_pages") or 0)
    structures = int(state.get("structures") or 0)
    if pages:
        st.progress(
            min(completed / pages, 1.0),
            text=f"Pages: {completed}/{pages} · Structures: {structures}",
        )
    if stage == "starting":
        st.info("Preparing extraction…")
    elif stage == "extracting":
        st.info("Analyzing pages…")
    elif stage == "page_complete":
        st.info(f"Page {state.get('page')} complete · {structures} structures found")
    elif stage == "results_ready":
        st.success("Done! Loading your results…")
    elif stage == "failed":
        st.error("Extraction did not complete. Please try again.")
    if st.session_state.get("panchem_live_error"):
        st.warning("Results could not load. Please try again.")
        if st.button("Retry loading results", key="retry-results"):
            st.session_state.pop("panchem_last_event", None)
            st.rerun()


live_progress()

st.divider()
st.subheader("Results")
artifact_id = st.session_state.get("active_artifact_id")
if artifact_id:
    blob = st.session_state.get(f"artifact-{artifact_id}")
    run_id = st.session_state.get("active_run_id")
    artifact_name = st.session_state.get("active_artifact_name", f"panChem-extraction-{run_id}")
    if blob:
        st.download_button(
            "Download results", blob, artifact_name + ".zip",
            mime="application/zip", key=f"download-{artifact_id}",
        )
        try:
            extracted, images = rows_from_artifact(blob)
            st.metric("Structures detected", len(extracted))
            if not extracted:
                st.info("No chemical structures were found on these pages.")
            state = st.session_state.setdefault(f"edits-{artifact_id}", {})
            reviewed = []
            for index, original in enumerate(extracted):
                rec = dict(original)
                label = f"Page {rec.get('page')} · Structure {rec.get('structure')}"
                name = rec.get("image", "")
                img = images.get(
                    name, next(
                        (data for p, data in images.items() if p.endswith("/" + name)),
                        None,
                    ),
                )
                with st.expander(label, expanded=index < 2):
                    edit_key = f"edit-{artifact_id}-{index}"
                    drawing_key = f"{artifact_id}:{index}"
                    active = st.session_state.get("ketcher_open")
                    if st.button(
                        "Close drawing editor" if active == drawing_key else "Edit drawing",
                        key=f"ketcher-toggle-{drawing_key}",
                    ):
                        st.session_state["ketcher_open"] = (
                            None if active == drawing_key else drawing_key
                        )
                        st.rerun()
                    if st.session_state.get("ketcher_open") == drawing_key:
                        if _ketcher is None:
                            st.warning("The drawing editor is temporarily unavailable.")
                        else:
                            current_smiles = st.session_state.get(edit_key, rec.get("smiles", ""))
                            drawing = _ketcher(
                                molfile=initial_molfile(current_smiles),
                                smiles=current_smiles,
                                label=label,
                                molecule_key=drawing_key,
                                key=f"ketcher-component-{drawing_key}",
                                default=None,
                            )
                            if isinstance(drawing, dict) and drawing.get("action") == "apply":
                                try:
                                    corrected, molblock = validated_ketcher_result(
                                        drawing, drawing_key
                                    )
                                    st.session_state[edit_key] = corrected
                                    st.session_state[f"ketcher-mol-{drawing_key}"] = {
                                        "smiles": corrected,
                                        "molfile": molblock,
                                    }
                                    st.session_state["ketcher_open"] = None
                                    st.rerun()
                                except ValueError as exc:
                                    st.warning(f"Please check the drawing: {exc}")
                    a, b, c = st.columns([1, 2, 1])
                    with a:
                        if img:
                            st.image(img, caption="From PDF", use_container_width=True)
                    with b:
                        value = st.text_area(
                            "SMILES",
                            value=rec.get("smiles", ""),
                            key=edit_key,
                        )
                        state[str(index)] = value
                        qa_status, canonical, inchikey, mol = assess(value)
                        st.caption({
                            "VALID_SYNTAX": "SMILES syntax: valid",
                            "MARKUSH_REVIEW": "Needs review: variable group",
                            "INVALID_SMILES": "SMILES syntax: invalid",
                            "MISSING_SMILES": "No SMILES detected",
                        }.get(qa_status, "Needs review"))
                        if rec.get("error"):
                            st.caption("This structure needs review.")
                        if qa_status == "MARKUSH_REVIEW":
                            st.info("Variable group detected. Please review this structure.")
                        if inchikey:
                            st.code(inchikey, language=None)
                    with c:
                        drawing = molecule_svg(mol) if mol else None
                        if drawing:
                            st.image(drawing, caption="2D preview", use_container_width=True)
                            st.download_button("Download SVG", drawing.encode("utf-8"),
                                file_name=f"panChem_{run_id}_page{rec.get('page')}_structure{rec.get('structure')}.svg",
                                mime="image/svg+xml", key=f"svg-{artifact_id}-{index}")
                        elif mol:
                            st.warning("2D preview is unavailable for this structure.")
                        else:
                            st.caption("Enter a valid SMILES to preview.")
                qa_status, canonical, inchikey, mol = assess(state[str(index)])
                rec.update(
                    original_smiles=original.get("smiles", ""),
                    reviewed_smiles=state[str(index)],
                    canonical_smiles=canonical,
                    inchikey=inchikey,
                    qa_status=qa_status,
                    source_run_id=run_id,
                )
                reviewed.append(rec)
            if reviewed:
                counts = {}
                for rec in reviewed:
                    counts[rec["qa_status"]] = counts.get(rec["qa_status"], 0) + 1
                st.write("Structure checks:", {
                    {
                        "VALID_SYNTAX": "SMILES syntax valid",
                        "MARKUSH_REVIEW": "Variable groups",
                        "INVALID_SMILES": "Invalid SMILES",
                        "MISSING_SMILES": "Missing SMILES",
                    }.get(key, key): value for key, value in counts.items()
                })
                st.download_button(
                    "Download reviewed results",
                    make_review_zip(
                        reviewed, images,
                        molfiles={
                            str(i): result_molfile(
                                row.get("reviewed_smiles", ""),
                                (
                                    st.session_state.get(f"ketcher-mol-{artifact_id}:{i}", {}).get("molfile", "")
                                    if st.session_state.get(f"ketcher-mol-{artifact_id}:{i}", {}).get("smiles")
                                    == row.get("reviewed_smiles") else ""
                                ),
                            )
                            for i, row in enumerate(reviewed)
                            if row.get("qa_status") == "VALID_SYNTAX"
                        },
                    ),
                    f"panChem_reviewed_{run_id}.zip",
                    mime="application/zip", key=f"reviewed-{artifact_id}",
                )
        except Exception as exc:
            import logging
            logging.exception("Could not display extracted structures")
            st.warning("Results could not be displayed. Please try again.")
else:
    st.caption("Your structures will appear here when extraction finishes.")

@st.fragment
def recent_jobs():
    """Lightweight job-history refresh; never rerender the chemical editor."""
    if not token:
        return
    with st.expander("Previous analyses", expanded=False):
        try:
            runs = api_request(
                "GET", f"{API}/actions/workflows/{WORKFLOW}/runs",
                token, params={"per_page": 12, "event": "push"},
            ).json().get("workflow_runs", [])
            if not runs:
                st.caption("No extraction jobs yet.")
                return
            completed = [
                r for r in runs if r.get("conclusion") == "success"
            ]
            if completed:
                selected = st.selectbox(
                    "Choose a previous analysis",
                    options=[r["id"] for r in completed],
                    key="recent-run-picker",
                    format_func=lambda run_id: next(
                        (format_eat(r.get("created_at"))
                         for r in completed if r["id"] == run_id),
                        "Previous analysis",
                    ),
                )
                if st.button("Open results", key="open-history"):
                    artifacts = api_request(
                        "GET", f"{API}/actions/runs/{selected}/artifacts", token,
                    ).json().get("artifacts", [])
                    available = [a for a in artifacts if not a.get("expired")]
                    if not available:
                        st.warning("This run has no downloadable results artifact.")
                    else:
                        art = available[0]
                        aid = art["id"]
                        if f"artifact-{aid}" not in st.session_state:
                            st.session_state[f"artifact-{aid}"] = api_request(
                                "GET", f"{API}/actions/artifacts/{aid}/zip", token,
                            ).content
                        st.session_state["manual_view_run_id"] = selected
                        st.session_state["active_artifact_id"] = aid
                        st.session_state["active_artifact_name"] = art["name"]
                        st.session_state["active_run_id"] = selected
                        st.rerun()
        except Exception as exc:
            import logging
            logging.exception("History retrieval failed")
            st.warning("Previous analyses could not be loaded.")

@st.fragment
def performance_diagnostics_panel():
    """On-demand timing inspection; separate from the user-facing live progress."""
    with st.expander("Performance diagnostics", expanded=False):
        current_artifact = st.session_state.get("active_artifact_id")
        current_run = st.session_state.get("active_run_id")
        blob = st.session_state.get(f"artifact-{current_artifact}") if current_artifact else None
        worker_perf = None
        if blob:
            try:
                worker_perf = performance_from_artifact(blob)
            except ValueError:
                st.caption("Timing file could not be read from this archive.")
            if worker_perf:
                st.caption("Worker measurements from the downloaded extraction artifact.")
                col_a, col_b, col_c = st.columns(3)
                col_a.metric("Worker total", f"{worker_perf.get('total_worker_seconds', 0):.1f} s")
                col_b.metric("Structures", int(worker_perf.get("total_structures", 0)))
                col_c.metric("Valid SMILES", int(worker_perf.get("valid_smiles", 0)))
                if worker_perf.get("model_import_seconds") is not None:
                    st.caption(
                        f"Model module import: {worker_perf['model_import_seconds']:.2f} s. "
                        "First inference may also load model weights."
                    )
                if worker_perf.get("pdf_fetch_seconds") is not None:
                    st.caption(f"PDF download: {worker_perf['pdf_fetch_seconds']:.2f} s")
                page_records = worker_perf.get("pages", [])
                if page_records:
                    st.dataframe(page_records, hide_index=True, use_container_width=True)
                st.caption(
                    "Render, segmentation and recognition are measured separately. "
                    "Worker total also includes initialization and result-writing overhead."
                )
            else:
                st.caption("This extraction predates per-page timing instrumentation.")
        else:
            st.caption("Select an extraction to see per-page measurements.")

        if not token:
            return
        targets = ["Extraction workflow", "Worker image builds"]
        target = st.selectbox("GitHub Actions timings", targets, key="perf-diagnostic-target")
        if st.button("Load / refresh timings", key="perf-load-github"):
            try:
                if target == "Extraction workflow":
                    if not current_run:
                        st.info("Open an extraction result first.")
                    else:
                        run = api_request(
                            "GET", f"{API}/actions/runs/{int(current_run)}", token
                        ).json()
                        jobs = api_request(
                            "GET", f"{API}/actions/runs/{int(current_run)}/jobs", token,
                            params={"per_page": 100},
                        ).json().get("jobs", [])
                        report = github_timing_report(run, jobs)
                        st.session_state["perf-github-report"] = {
                            "target": target, "run": int(current_run), "reports": [report]
                        }
                else:
                    runs = api_request(
                        "GET", f"{API}/actions/workflows/build-worker.yml/runs", token,
                        params={"per_page": 10},
                    ).json().get("workflow_runs", [])
                    selected = [r for r in runs if r.get("status") == "completed"][:2]
                    reports = []
                    for run in selected:
                        jobs = api_request(
                            "GET", f"{API}/actions/runs/{int(run['id'])}/jobs", token,
                            params={"per_page": 100},
                        ).json().get("jobs", [])
                        reports.append(github_timing_report(run, jobs))
                    st.session_state["perf-github-report"] = {
                        "target": target, "run": None, "reports": reports
                    }
                    if not reports:
                        st.info("No completed worker builds were found.")
            except Exception:
                import logging
                logging.exception("Performance timing retrieval failed")
                st.warning("GitHub timing details could not be loaded.")

        saved = st.session_state.get("perf-github-report")
        if saved and saved.get("target") == target and (
            target == "Worker image builds" or saved.get("run") == current_run
        ):
            reports = saved.get("reports", [])
            if target == "Worker image builds" and reports:
                st.dataframe([
                    {
                        "Run ID": item["run_id"],
                        "Status": item["workflow_status"],
                        "Conclusion": item["workflow_conclusion"],
                        "Queue (s)": item["queue_seconds"],
                        "Job (s)": item["job_seconds"],
                    }
                    for item in reports
                ], hide_index=True, use_container_width=True)
            for item in reports[:1]:
                if item.get("verified_success"):
                    st.caption(f"Run {item['run_id']}: completed / success (workflow and job).")
                else:
                    st.caption(
                        f"Run {item['run_id']}: workflow "
                        f"{item.get('workflow_status')} / {item.get('workflow_conclusion')}; "
                        f"job {item.get('job_status')} / {item.get('job_conclusion')}. "
                        "Overall success is not verified."
                    )
                if item.get("queue_seconds") is not None:
                    st.caption(f"Queue to job start: {item['queue_seconds']:.1f} s")
                if item.get("job_seconds") is not None:
                    st.caption(f"Job duration: {item['job_seconds']:.1f} s")
                rows = [
                    {"Step": row["name"], "Seconds": row["seconds"],
                     "Conclusion": row["conclusion"]}
                    for row in item.get("steps", [])
                    if row.get("seconds") is not None
                ]
                if rows:
                    st.dataframe(rows, hide_index=True, use_container_width=True)
            st.caption(
                "GitHub job timing includes Docker initialization, model-cache restoration "
                "and artifact upload. These are measured from official step timestamps."
            )
        else:
            reports = []

        if worker_perf or reports:
            payload = {
                "worker": worker_perf,
                "github": reports,
                "note": "Performance measurements only; not a chemical accuracy validation.",
            }
            with st.popover("Copy timing report"):
                st.caption("Use the copy icon in the upper-right corner to copy the full report.")
                st.code(json.dumps(payload, indent=2), language="json", wrap_lines=True)


recent_jobs()
performance_diagnostics_panel()

st.caption("Check extracted structures against the original PDF before using them.")
