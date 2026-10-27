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
        try:
            with st.spinner("Uploading PDF and starting DECIMER job..."):
                api_request("PUT", f"{API}/contents/{pdf_path}", token,
                    json={"message": f"panChem PDF job {job_id} pages={\",\".join(map(str,pages))}", "content": base64.b64encode(data).decode("ascii")})
            st.session_state["last_job_id"] = job_id
            st.success(f"Uploaded job {job_id}. GitHub Actions starts automatically on push; click Refresh jobs to check progress.")
        except Exception as exc:
            st.error(str(exc))

st.divider()
st.subheader("Job status and downloads")
if token:
    if st.button("Refresh jobs"):
        st.rerun()
    try:
        runs = api_request("GET", f"{API}/actions/workflows/{WORKFLOW}/runs", token,
                           params={"per_page": 25, "event": "push"}).json().get("workflow_runs", [])
        if not runs:
            st.info("No Actions jobs found yet.")
        for run in runs:
            run_id = run["id"]
            label = f"Run #{run_id} · {run.get('conclusion') or run['status']} · {run['created_at']}"
            with st.expander(label, expanded=False):
                st.link_button("View logs on GitHub", run["html_url"])
                if run["status"] == "completed":
                    artifacts = api_request("GET", f"{API}/actions/runs/{run_id}/artifacts",token).json().get("artifacts", [])
                    if not artifacts:
                        st.warning("No results artifact. Check workflow logs.")
                    for artifact in artifacts:
                        if artifact.get("expired"): continue
                        aid = artifact["id"]
                        if st.button(f"Retrieve {artifact['name']}",key=f"fetch-{aid}"):
                            try:
                                blob=api_request("GET", f"{API}/actions/artifacts/{aid}/zip", token).content
                                st.session_state[f"artifact-{aid}"]=blob
                            except Exception as e:
                                st.error(str(e))
                        blob=st.session_state.get(f"artifact-{aid}")
                        if blob:
                            st.download_button("Download results ZIP",blob,artifact["name"]+".zip",
                                               "application/zip",key=f"download-{aid}")
                            try:
                                extracted, images = rows_from_artifact(blob)
                                st.caption(f"{len(extracted)} detected structures")
                                edits_key = f"edits-{aid}"
                                if edits_key not in st.session_state:
                                    st.session_state[edits_key] = {}
                                state = st.session_state[edits_key]
                                export = []
                                for index, original in enumerate(extracted):
                                    rec = dict(original)
                                    label = f"Page {rec.get('page')} · Structure {rec.get('structure')}"
                                    name = rec.get("image", "")
                                    img = images.get(name, next((data for p, data in images.items() if p.endswith("/"+name)), None))
                                    with st.expander(label, expanded=index < 2):
                                        a, b, c = st.columns([1, 2, 1])
                                        with a:
                                            if img:
                                                st.image(img, caption="Original detected image", use_container_width=True)
                                        with b:
                                            value = st.text_area("Review or correct SMILES",
                                                value=rec.get("smiles", ""),
                                                key=f"edit-{aid}-{index}")
                                            state[str(index)] = value
                                            status, canonical, inchikey, mol = assess(value)
                                            st.write("QA:", status)
                                            if rec.get("error"):
                                                st.warning("DECIMER recognition: " + rec["error"])
                                            if status == "MARKUSH_REVIEW":
                                                st.info("Variable substituent detected. Requires explicit chemical interpretation; not treated as a concrete molecule.")
                                            if inchikey:
                                                st.code(inchikey, language=None)
                                        with c:
                                            drawing = molecule_svg(mol) if mol else None
                                            if drawing:
                                                st.image(drawing, caption="RDKit reconstruction")
                                            elif mol:
                                                st.info("2D drawing unavailable on this deployment. SMILES validation remains enabled.")
                                            else:
                                                st.caption("No valid 2D structure to render")
                                    status, canonical, inchikey, mol = assess(state[str(index)])
                                    rec.update(original_smiles=original.get("smiles", ""),
                                               reviewed_smiles=state[str(index)],
                                               canonical_smiles=canonical,
                                               inchikey=inchikey,
                                               qa_status=status,
                                               source_run_id=run_id)
                                    export.append(rec)
                                statuses = {}
                                for rec in export:
                                    statuses[rec["qa_status"]] = statuses.get(rec["qa_status"], 0) + 1
                                st.write("Chemical QA summary", statuses)
                                st.download_button("Download reviewed CSV, SMI, JSON and images",
                                    make_review_zip(export, images), f"panChem_reviewed_{run_id}.zip",
                                    mime="application/zip", key=f"reviewed-{aid}")

                            except Exception as exc:
                                st.warning(f"Preview unavailable: {exc}")
    except Exception as exc:
        st.error(f"Actions status unavailable: {exc}")
else:
    st.info("Configure the GitHub token to enable submission, job tracking and downloads.")
st.caption("Validate chemical structures against the source figures. Valid SMILES syntax does not guarantee correct recognition.")
