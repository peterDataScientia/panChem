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
st.caption("Automated chemical structure extraction using GitHub Actions + official DECIMER models.")

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
with st.sidebar:
    st.header("Backend")
    st.write("GitHub Actions · DECIMER Segmentation + Transformer")
    st.link_button("Open Actions", f"https://github.com/{REPO}/actions/workflows/{WORKFLOW}")
    if token:
        st.success("GitHub token configured")
        if st.button("Diagnose GitHub token permissions"):
            try:
                auth = api_request("GET", "https://api.github.com/user", token).json()
                repo_info = api_request("GET", API, token).json()
                st.write("Authenticated GitHub account:", auth.get("login", "unknown"))
                st.write("Repository:", repo_info.get("full_name", "unknown"))
                st.json(repo_info.get("permissions", {}))
                response = requests.get(f"{API}/contents/README.md",
                    headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
                    timeout=20)
                st.write("Contents read status:", response.status_code)
                st.caption("Account push permission does not guarantee fine-grained PAT Contents write access.")
            except Exception as exc:
                st.error(f"Authentication diagnostic failed: {exc}")
        if st.button("Test Contents WRITE permission", key="write-probe"):
            probe_path = f"jobs/permission-probe-{uuid.uuid4().hex[:10]}.txt"
            headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                       "X-GitHub-Api-Version": "2022-11-28"}
            try:
                response = requests.put(f"{API}/contents/{probe_path}", headers=headers,
                    json={"message": "panChem permission diagnostic", "content": base64.b64encode(b"panChem write diagnostic\\n").decode("ascii")},
                    timeout=45)
                st.write("Actual Contents WRITE status:", response.status_code)
                st.write("GitHub required permission:", response.headers.get("X-Accepted-GitHub-Permissions", "Not provided"))
                st.write("OAuth scopes:", response.headers.get("X-OAuth-Scopes", "Not provided"))
                if response.status_code == 201:
                    st.success("Contents write works. Upload authorization is not the blocker for this token.")
                    try:
                        sha = response.json()["content"]["sha"]
                        cleanup = requests.delete(f"{API}/contents/{probe_path}", headers=headers,
                            json={"message": "Remove panChem permission probe", "sha": sha}, timeout=45)
                        st.caption(f"Probe cleanup status: {cleanup.status_code}")
                    except Exception as cleanup_error:
                        st.warning(f"Probe cleanup failed: {cleanup_error}")
                else:
                    try:
                        payload = response.json()
                        st.error(f"GitHub message: {payload.get('message', response.reason)}")
                        st.caption(str(payload.get("documentation_url", "")))
                    except ValueError:
                        st.error(f"GitHub response: {response.reason}")
                    st.info("If status is 403, check the token's repository selection, effective Contents: Read and write permission, pending approval, and whether Streamlit Secrets holds the latest token.")
            except requests.RequestException as exc:
                st.error(f"Write diagnostic connection failed: {exc}")
    else:
        st.warning("Add GITHUB_TOKEN in Streamlit App Settings → Secrets. GitHub token requires Contents read/write permission; Actions write is not required.")
    st.caption("PDFs uploaded to this public repository's jobs/ directory will be publicly accessible until removed.")

uploaded = st.file_uploader("1. Upload scientific PDF", type="pdf")
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
    st.subheader("2. Browse PDF pages")
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
            "◀ Previous", key="preview_previous", use_container_width=True,
            disabled=st.session_state["pdf_preview_page"] <= 1,
            on_click=move_preview, args=(-1,),
        )
    with slider_col:
        if count > 1:
            viewed = st.slider(
                "Slide through all PDF pages", min_value=1, max_value=count,
                step=1, key="pdf_preview_page",
                help="Drag the slider to preview any page of this PDF.",
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
    st.caption(f"Previewing page {viewed} of {count}. All pages are available on the slider.")
    fullscreen_preview = st.toggle(
        "Interactive full-page PDF viewer (zoom, page slider, swipe, fullscreen)",
        value=False, key="interactive_pdf_viewer",
        help="Controls remain available inside the full-page viewer and fullscreen mode.",
    )
    if fullscreen_preview:
        components.html(full_page_viewer(data, initial_page=viewed), height=720, scrolling=False)
        st.caption(
            "Use controls inside the PDF viewer: previous/next, page number, slider, zoom, "
            "fit width/page and fullscreen. On mobile, swipe sideways to change pages. "
            "Browser fullscreen availability depends on device and iframe permissions."
        )
    else:
        st.image(preview(data, viewed), caption=f"PDF page {viewed} / {count}", width=580)

    selected = st.text_input(
        "3. Pages to extract", value="1",
        help="Examples: 1,3,5-8. Sliding the preview does not change extraction selection.",
    )
    try:
        pages = pages_list(selected, count)
        st.caption(f"{len(pages)} pages selected: {', '.join(map(str,pages))}")
    except ValueError as e:
        st.error(str(e))
        pages = []
    if st.button("4. Extract all structures with GitHub Actions", type="primary", disabled=not token or not pages):
        job_id = uuid.uuid4().hex[:12]
        pdf_path = f"jobs/{job_id}/paper.pdf"
        page_spec = ",".join(map(str, pages))
        try:
            with st.spinner("Uploading PDF and starting DECIMER job..."):
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
            st.success(f"Job {job_id} submitted. Live tracking and results will update automatically below.")
        except Exception as exc:
            st.error(str(exc))

@st.fragment
def live_progress():
    """SSE push updates; no timer and no GitHub polling."""
    st.subheader("🔴 Live DECIMER extraction · event-driven")
    wanted = st.session_state.get("last_job_id", "")
    if not token:
        st.info("Configure GitHub token to enable extraction results.")
        return
    if not wanted:
        st.caption("Upload a PDF to start a live event stream. No timed refresh is used.")
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
        st.info("Waiting for the DECIMER worker to emit its first event…")
        st.caption("A persistent live connection replaces the old five-second polling.")
        return
    stage = state.get("stage")
    pages = int(state.get("total_pages") or 0)
    completed = int(state.get("completed_pages") or 0)
    structures = int(state.get("structures") or 0)
    if pages:
        st.progress(
            min(completed / pages, 1.0),
            text=f"Pages completed: {completed}/{pages} · Structures detected: {structures}",
        )
    if stage == "starting":
        st.info("Runner started. Setting up DECIMER…")
    elif stage == "extracting":
        st.info("DECIMER is processing the selected PDF pages.")
    elif stage == "page_complete":
        st.success(
            f"Page {state.get('page')} finished: "
            f"{state.get('page_structures', 0)} structures. "
            f"Total detected: {structures}"
        )
    elif stage == "results_ready":
        st.success("Extraction archive is ready and will load automatically.")
    elif stage == "failed":
        st.error("The GitHub extraction job failed. Inspect its workflow logs.")
    if st.session_state.get("panchem_live_error"):
        st.warning("Results retrieval: " + st.session_state["panchem_live_error"])
        if st.button("Retry loading results", key="retry-results"):
            st.session_state.pop("panchem_last_event", None)
            st.rerun()
    st.caption(
        "Push notifications arrive as DECIMER emits them. No fixed five-second "
        "timer; if the live connection drops, the browser reconnects automatically."
    )


live_progress()

st.divider()
st.subheader("Extracted structures · automatic results")
artifact_id = st.session_state.get("active_artifact_id")
if artifact_id:
    blob = st.session_state.get(f"artifact-{artifact_id}")
    run_id = st.session_state.get("active_run_id")
    artifact_name = st.session_state.get("active_artifact_name", f"panChem-extraction-{run_id}")
    if blob:
        st.download_button(
            "Download original results ZIP", blob, artifact_name + ".zip",
            mime="application/zip", key=f"download-{artifact_id}",
        )
        try:
            extracted, images = rows_from_artifact(blob)
            st.metric("Structures detected", len(extracted))
            if not extracted:
                st.info("Extraction succeeded, but no chemical structures were detected.")
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
                            st.image(img, caption="Original detected structure", use_container_width=True)
                    with b:
                        value = st.text_area(
                            "Review or correct SMILES",
                            value=rec.get("smiles", ""),
                            key=f"edit-{artifact_id}-{index}",
                        )
                        state[str(index)] = value
                        qa_status, canonical, inchikey, mol = assess(value)
                        st.write("QA:", qa_status)
                        if rec.get("error"):
                            st.warning("DECIMER recognition: " + rec["error"])
                        if qa_status == "MARKUSH_REVIEW":
                            st.info("Variable substituent detected. Chemical review is required.")
                        if inchikey:
                            st.code(inchikey, language=None)
                    with c:
                        drawing = molecule_svg(mol) if mol else None
                        if drawing:
                            st.image(drawing, caption="2D structure from reviewed SMILES", use_container_width=True)
                            st.download_button("Download 2D SVG", drawing.encode("utf-8"),
                                file_name=f"panChem_{run_id}_page{rec.get('page')}_structure{rec.get('structure')}.svg",
                                mime="image/svg+xml", key=f"svg-{artifact_id}-{index}")
                        elif mol:
                            st.warning("2D rendering failed. Check application logs for RDKit or SVG errors.")
                        else:
                            st.caption("No valid 2D molecule")
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
                st.write("Chemical QA summary:", counts)
                st.download_button(
                    "Download reviewed CSV, SMI, JSON and images",
                    make_review_zip(reviewed, images),
                    f"panChem_reviewed_{run_id}.zip",
                    mime="application/zip", key=f"reviewed-{artifact_id}",
                )
        except Exception as exc:
            st.warning(f"Cannot display extracted structures: {exc}")
else:
    st.caption("Results will appear here automatically when the current extraction succeeds.")

@st.fragment
def recent_jobs():
    """Lightweight job-history refresh; never rerender the chemical editor."""
    if not token:
        return
    with st.expander("Recent jobs · automatically updated", expanded=False):
        try:
            runs = api_request(
                "GET", f"{API}/actions/workflows/{WORKFLOW}/runs",
                token, params={"per_page": 12, "event": "push"},
            ).json().get("workflow_runs", [])
            if not runs:
                st.caption("No extraction jobs yet.")
                return
            for run in runs:
                status = run.get("conclusion") or run.get("status")
                msg = (run.get("head_commit") or {}).get("message", "")
                st.write(f"Run #{run['id']}: {status} · {msg}")
            completed = [
                r for r in runs if r.get("conclusion") == "success"
            ]
            if completed:
                selected = st.selectbox(
                    "Load results from a previous successful run",
                    options=[r["id"] for r in completed],
                    key="recent-run-picker",
                )
                if st.button("Open previous results", key="open-history"):
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
            st.warning(f"Recent job list unavailable: {exc}")

recent_jobs()

st.caption("Validate chemical structures against the source figures. Valid SMILES syntax does not guarantee correct recognition.")
