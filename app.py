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
        raise RuntimeError(f"GitHub API {response.status_code}: {response.text[:500]}")
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
                st.caption("The token is never printed. A 403 on upload despite repository read access typically means insufficient effective Contents write permission or an outdated token in Streamlit Secrets.")
            except Exception as exc:
                st.error(f"Authentication diagnostic failed: {exc}")
    else:
        st.warning("Add GITHUB_TOKEN in Streamlit App Settings → Secrets. GitHub token requires Contents read/write and Actions read/write permissions.")
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
                    json={"message": f"panChem PDF job {job_id}", "content": base64.b64encode(data).decode("ascii")})
                api_request("POST", f"{API}/actions/workflows/{WORKFLOW}/dispatches", token,
                    json={"ref":"main", "inputs":{"pdf_path":pdf_path,"pages":",".join(map(str,pages)), "job_id":job_id}})
            st.session_state["last_job_id"] = job_id
            st.success(f"Submitted job {job_id}. Click Refresh jobs to check progress.")
        except Exception as exc:
            st.error(str(exc))

st.divider()
st.subheader("Job status and downloads")
if token:
    if st.button("Refresh jobs"):
        st.rerun()
    try:
        runs = api_request("GET", f"{API}/actions/workflows/{WORKFLOW}/runs", token,
                           params={"per_page": 25, "event": "workflow_dispatch"}).json().get("workflow_runs", [])
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
                                with zipfile.ZipFile(io.BytesIO(blob)) as z:
                                    candidates=[n for n in z.namelist() if n.endswith("results.csv")]
                                    if candidates:
                                        rows=list(csv.DictReader(io.StringIO(z.read(candidates[0]).decode())))
                                        st.dataframe(rows,use_container_width=True)
                                        st.caption(f"{len(rows)} detected structures")
                            except Exception as exc:
                                st.warning(f"Preview unavailable: {exc}")
    except Exception as exc:
        st.error(f"Actions status unavailable: {exc}")
else:
    st.info("Configure the GitHub token to enable submission, job tracking and downloads.")
st.caption("Validate chemical structures against the source figures. Valid SMILES syntax does not guarantee correct recognition.")
