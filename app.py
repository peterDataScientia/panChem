import base64
import csv
import hashlib
import io
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

_live_events = components.declare_component(
    "panchem_live_events", path=str(Path(__file__).parent / "components" / "live_progress")
)

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
    st.subheader("2. Preview pages")
    pdf_hash = hashlib.sha256(data).hexdigest()
    if st.session_state.get("preview_pdf_hash") != pdf_hash:
        st.session_state["preview_pdf_hash"] = pdf_hash
        st.session_state["pdf_preview_page"] = 1

    def move_preview(delta):
        current = st.session_state.get("pdf_preview_page", 1)
        st.session_state["pdf_preview_page"] = max(1, min(count, current + delta))

    previous_col, slider_col, next_col = st.columns([1, 6, 1], vertical_alignment="bottom")
    with previous_col:
        st.button(
            "◀ Back", key="preview_previous", use_container_width=True,
            disabled=st.session_state["pdf_preview_page"] <= 1,
            on_click=move_preview, args=(-1,),
        )
    with slider_col:
        if count > 1:
            viewed = st.slider(
                "Page", min_value=1, max_value=count,
                step=1, key="pdf_preview_page",
                help="Slide to any page.",
            )
        else:
            viewed = 1
            st.caption("Single-page PDF")
    with next_col:
        st.button(
            "Next ▶", key="preview_next", use_container_width=True,
            disabled=st.session_state["pdf_preview_page"] >= count,
            on_click=move_preview, args=(1,),
        )
    fullscreen_preview = st.toggle(
        "Large interactive preview",
        value=False, key="interactive_pdf_viewer",
        help="Browse, zoom and view pages fullscreen.",
    )
    if fullscreen_preview:
        components.html(full_page_viewer(data, initial_page=viewed), height=720, scrolling=False)
    else:
        st.image(preview(data, viewed), caption=f"PDF page {viewed} / {count}", width=580)

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
                    a, b, c = st.columns([1, 2, 1])
                    with a:
                        if img:
                            st.image(img, caption="From PDF", use_container_width=True)
                    with b:
                        value = st.text_area(
                            "SMILES",
                            value=rec.get("smiles", ""),
                            key=f"edit-{artifact_id}-{index}",
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
                    make_review_zip(reviewed, images),
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
                        (r.get("created_at", "")[:16].replace("T", " ") + " UTC"
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

recent_jobs()

st.caption("Check extracted structures against the original PDF before using them.")
