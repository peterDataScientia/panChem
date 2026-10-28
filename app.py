import base64
import csv
import hashlib
import io
import time
import uuid
import zipfile
from urllib.parse import urlparse

import pypdfium2 as pdfium
import requests
import streamlit as st
from PIL import Image
from chemqa import assess, molecule_svg, rows_from_artifact, make_review_zip

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
    col1, col2 = st.columns([2, 1])
    with col1:
        selected = st.text_input("2. Pages to extract", value="1", help="Examples: 1,3,5-8")
    with col2:
        viewed = st.number_input("Preview page", min_value=1, max_value=count, value=1)
    st.image(preview(data, viewed), caption=f"Page {viewed} / {count}", width=580)
    try:
        pages = pages_list(selected, count)
        st.caption(f"{len(pages)} pages selected: {', '.join(map(str,pages))}")
    except ValueError as e:
        st.error(str(e))
        pages = []
    if st.button("3. Extract all structures with GitHub Actions", type="primary", disabled=not token or not pages):
        job_id = uuid.uuid4().hex[:12]
        pdf_path = f"jobs/{job_id}/paper.pdf"
        page_spec = ",".join(map(str, pages))
        try:
            with st.spinner("Uploading PDF and starting DECIMER job..."):
                api_request("PUT", f"{API}/contents/{pdf_path}", token,
                    json={"message": f"panChem PDF job {job_id} pages={page_spec}", "content": base64.b64encode(data).decode("ascii")})
            st.session_state["last_job_id"] = job_id
            st.session_state.pop("manual_view_run_id", None)
            st.session_state.pop("active_artifact_id", None)
            st.session_state.pop("active_artifact_name", None)
            st.session_state.pop("active_run_id", None)
            st.success(f"Job {job_id} submitted. Live tracking and results will update automatically below.")
        except Exception as exc:
            st.error(str(exc))

@st.fragment(run_every="5s")
def live_progress():
    """Only this monitor redraws; PDF upload and SMILES editors stay untouched."""
    st.subheader("🔴 Live DECIMER extraction")
    if not token:
        st.info("Configure GITHUB_TOKEN to enable automatic job tracking.")
        return
    wanted = st.session_state.get("last_job_id")
    try:
        response = api_request(
            "GET", f"{API}/actions/workflows/{WORKFLOW}/runs", token,
            params={"per_page": 30, "event": "push"},
        ).json()
        runs = response.get("workflow_runs", [])
        if wanted:
            target = next(
                (run for run in runs
                 if f"panChem PDF job {wanted} " in
                 (run.get("head_commit") or {}).get("message", "")),
                None,
            )
            if target is None:
                st.info(f"Job {wanted} submitted. Waiting for GitHub Actions to register it…")
                st.caption("Checking automatically every 5 seconds. No Refresh button needed.")
                return
        else:
            target = next(
                (run for run in runs
                 if (run.get("head_commit") or {}).get("message", "")
                 .startswith("panChem PDF job ")),
                None,
            )
            if target is None:
                st.caption("Upload a PDF to see its extraction progress in real time.")
                return

        run_id = target["id"]
        status = target.get("conclusion") or target.get("status", "unknown")
        message = (target.get("head_commit") or {}).get("message", "")
        st.write(f"**{message or ('Run #' + str(run_id))}**")
        st.caption(f"GitHub Actions run #{run_id} · {status.upper()} · polling every 5 seconds")
        st.link_button("Open detailed GitHub logs", target["html_url"])

        job_response = api_request(
            "GET", f"{API}/actions/runs/{run_id}/jobs", token,
            params={"per_page": 30},
        ).json()
        steps = [step for job in job_response.get("jobs", [])
                 for step in job.get("steps", [])]
        meaningful = [
            step for step in steps if not step.get("name", "").startswith(
                ("Post ", "Complete job", "Stop containers")
            )
        ]
        passed = sum(step.get("conclusion") == "success" for step in meaningful)
        total = len(meaningful)
        if total:
            st.progress(passed / total, text=f"Pipeline: {passed}/{total} stages passed")
        active = next(
            (step for step in meaningful if step.get("status") == "in_progress"),
            None,
        )
        failed = next(
            (step for step in meaningful if step.get("conclusion") == "failure"),
            None,
        )
        if failed or status in ("failure", "cancelled", "timed_out"):
            st.error(f"Extraction failed: {failed['name']}" if failed else f"Job {status}.")
        elif active:
            st.info("⚙️ Running now: " + active["name"])
        elif status == "queued":
            st.info("⏳ Waiting for a runner.")
        elif status == "success":
            st.success("Extraction complete. Loading detected structures automatically…")
        else:
            st.info("GitHub is preparing or finishing the job.")
        if meaningful:
            with st.expander("Live pipeline steps", expanded=bool(failed)):
                for step in meaningful:
                    state = step.get("conclusion") or step.get("status", "pending")
                    symbol = (
                        "✅" if state == "success"
                        else "❌" if state == "failure"
                        else "🔄" if state == "in_progress"
                        else "⏳"
                    )
                    st.write(f"{symbol} {step['name']} — {state}")

        if status == "success":
            artifact_info = api_request(
                "GET", f"{API}/actions/runs/{run_id}/artifacts", token,
            ).json()
            artifacts = [
                artifact for artifact in artifact_info.get("artifacts", [])
                if not artifact.get("expired")
            ]
            if not artifacts:
                st.info("Job succeeded; waiting for its ZIP artifact to appear.")
                return
            artifact = artifacts[0]
            aid = artifact["id"]
            cache_key = f"artifact-{aid}"
            if cache_key not in st.session_state:
                st.session_state[cache_key] = api_request(
                    "GET", f"{API}/actions/artifacts/{aid}/zip", token,
                ).content
            if (st.session_state.get("manual_view_run_id") is None
                    and st.session_state.get("active_artifact_id") != aid):
                st.session_state["active_artifact_id"] = aid
                st.session_state["active_artifact_name"] = artifact["name"]
                st.session_state["active_run_id"] = run_id
                st.rerun()
            st.success("Results available below. No manual retrieval required.")
    except Exception as exc:
        st.warning(f"Live GitHub update temporarily unavailable: {exc}")


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
                            st.image(drawing, caption="RDKit reconstruction")
                        elif mol:
                            st.info("2D renderer unavailable. SMILES validation remains enabled.")
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

@st.fragment(run_every="15s")
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
