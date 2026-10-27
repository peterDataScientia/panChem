import csv
import hashlib
import io
import tempfile
import traceback
from pathlib import Path
from zipfile import ZipFile

import pypdfium2 as pdfium
import streamlit as st
from PIL import Image
from rdkit import Chem

st.set_page_config(page_title="panChem | Automatic PDF to SMILES", page_icon="🧪", layout="wide")
st.title("🧪 panChem")
st.caption("Choose PDF pages → detect every chemical structure automatically → predict and review SMILES.")

@st.cache_data(show_spinner=False, max_entries=32)
def page_png(pdf_bytes: bytes, page: int, scale: float = 2.0) -> bytes:
    pdf = pdfium.PdfDocument(pdf_bytes)
    try:
        rendered = pdf[page - 1].render(scale=scale).to_pil().convert("RGB")
        buf = io.BytesIO()
        rendered.save(buf, format="PNG")
        return buf.getvalue()
    finally:
        pdf.close()

def syntax_check(smiles: str):
    if not smiles.strip():
        return "Missing", ""
    mol = Chem.MolFromSmiles(smiles)
    return ("Valid SMILES syntax", Chem.MolToSmiles(mol)) if mol else ("Invalid SMILES syntax", "")

def extract_page(pdf_bytes: bytes, page: int):
    from decimer_segmentation import segment_chemical_structures_from_file
    from DECIMER import predict_SMILES
    png = page_png(pdf_bytes, page, 300 / 72)
    with tempfile.TemporaryDirectory(prefix="panchem_") as temp:
        page_path = Path(temp) / "page.png"
        page_path.write_bytes(png)
        segments, boxes = segment_chemical_structures_from_file(
            str(page_path), expand=True, return_bboxes=True
        )
        records = []
        for idx, (segment, box) in enumerate(zip(segments, boxes), 1):
            image = Image.fromarray(segment).convert("RGB")
            out = Path(temp) / f"structure_{idx}.png"
            image.save(out)
            try:
                prediction = str(predict_SMILES(str(out)))
                error = ""
            except Exception as exc:
                prediction = ""
                error = f"{type(exc).__name__}: {exc}"
            records.append(dict(page=page, ordinal=idx, bbox=str(tuple(int(x) for x in box)),
                                image=out.read_bytes(), original_smiles=prediction,
                                smiles=prediction, error=error))
        return records

def parse_pages(expression: str, count: int):
    result = set()
    for token in expression.replace(" ", "").split(","):
        if not token:
            continue
        if "-" in token:
            parts = token.split("-")
            if len(parts) != 2 or not all(p.isdigit() for p in parts):
                raise ValueError(f"Invalid page range: {token}")
            a, b = map(int, parts)
            if a > b:
                raise ValueError("Page ranges must be ascending.")
            result.update(range(a, b + 1))
        elif token.isdigit():
            result.add(int(token))
        else:
            raise ValueError(f"Invalid page number: {token}")
    if not result or min(result) < 1 or max(result) > count:
        raise ValueError(f"Select pages between 1 and {count}.")
    return sorted(result)


st.divider()
st.subheader("GitHub Actions · Batch inference")
st.caption("Run DECIMER on GitHub's CPU runner. Supply an HTTPS PDF URL that the runner can download. Unpublished or private PDFs should not use public links.")
remote_url = st.text_input("PDF HTTPS URL (GitHub Actions)", key="actions_pdf_url", placeholder="https://example.org/paper.pdf")
remote_pages = st.text_input("Pages (GitHub Actions)", value="1", key="actions_pages")
try:
    token = st.secrets.get("GITHUB_TOKEN", "")
except Exception:
    token = ""
api_base = "https://api.github.com/repos/peterDataScientia/panChem"
if not token:
    st.info("To dispatch jobs from Streamlit, configure GITHUB_TOKEN in Streamlit secrets with Actions: write and Actions: read permissions for panChem.")
else:
    import requests
    gh_headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if st.button("Run DECIMER on GitHub Actions", type="primary"):
        from urllib.parse import urlparse
        parsed = urlparse(remote_url)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            st.error("Use a valid HTTPS PDF URL.")
        elif not remote_pages.strip():
            st.error("Specify the pages.")
        else:
            try:
                response = requests.post(
                    api_base + "/actions/workflows/decimer.yml/dispatches",
                    headers=gh_headers,
                    json={"ref": "main", "inputs": {"pdf_url": remote_url, "pages": remote_pages}},
                    timeout=25,
                )
                response.raise_for_status()
                st.success("Workflow dispatched. Check the run list below for status/results.")
            except requests.RequestException as exc:
                st.error(f"GitHub API dispatch error: {exc}")
    if st.button("Refresh GitHub Actions jobs"):
        st.session_state["actions_refresh"] = st.session_state.get("actions_refresh", 0) + 1
    try:
        runs_response = requests.get(api_base + "/actions/workflows/decimer.yml/runs",
                                     headers=gh_headers, params={"per_page": 10}, timeout=20)
        runs_response.raise_for_status()
        for run in runs_response.json().get("workflow_runs", []):
            run_id = run["id"]
            status = run.get("conclusion") or run.get("status", "unknown")
            with st.expander(f"Run {run_id}: {status} · {run.get('created_at', '')}"):
                st.link_button("Open GitHub Actions run", run["html_url"])
                if run.get("status") == "completed":
                    resp = requests.get(api_base + f"/actions/runs/{run_id}/artifacts",
                                        headers=gh_headers, timeout=20)
                    if resp.ok:
                        for artifact in resp.json().get("artifacts", []):
                            if artifact.get("expired"):
                                continue
                            if st.button(f"Retrieve ZIP: {artifact['name']}", key=f"get_{artifact['id']}"):
                                try:
                                    download = requests.get(api_base + f"/actions/artifacts/{artifact['id']}/zip",
                                                            headers=gh_headers, timeout=60)
                                    download.raise_for_status()
                                    st.session_state[f"artifact_{artifact['id']}"] = download.content
                                except requests.RequestException as exc:
                                    st.error(f"Artifact download failed: {exc}")
                            content = st.session_state.get(f"artifact_{artifact['id']}")
                            if content:
                                st.download_button(f"Download {artifact['name']}.zip", content,
                                                   f"{artifact['name']}.zip", "application/zip",
                                                   key=f"save_{artifact['id']}")
    except requests.RequestException as exc:
        st.error(f"Could not load workflow status: {exc}")
st.divider()

upload = st.file_uploader("Upload research paper", type=["pdf"])
if not upload:
    st.info("Upload a PDF and select the pages to extract. No cropping is required.")
    st.stop()

pdf_data = upload.getvalue()
if len(pdf_data) > 25 * 1024 * 1024:
    st.error("File must not exceed 25 MB.")
    st.stop()
try:
    doc = pdfium.PdfDocument(pdf_data)
    page_count = len(doc)
    doc.close()
except Exception as exc:
    st.error(f"Unable to read PDF: {exc}")
    st.stop()

key = hashlib.sha256(pdf_data).hexdigest()
if st.session_state.get("document_key") != key:
    st.session_state.document_key = key
    st.session_state.extractions = []
    st.session_state.last_selection = []

st.write(f"**{upload.name}** · {page_count} pages")
left, right = st.columns([2, 1])
with left:
    page_selection = st.text_input("Pages to process", value="1", placeholder="e.g. 3,5,8-10")
with right:
    preview_page = st.number_input("Preview page", min_value=1, max_value=page_count, value=1)
st.image(page_png(pdf_data, preview_page, 1.4), caption=f"Page {preview_page}", width=600)

try:
    pages = parse_pages(page_selection, page_count)
    st.caption(f"Selected: {', '.join(map(str, pages))} ({len(pages)} pages)")
except ValueError as exc:
    st.error(str(exc))
    pages = []

if st.button("🔎 Automatically extract structures and SMILES", type="primary", disabled=not pages):
    st.session_state.extractions = []
    progress = st.progress(0, text="Loading DECIMER models...")
    all_records = []
    for i, page in enumerate(pages):
        try:
            found = extract_page(pdf_data, page)
            all_records.extend(found)
            st.write(f"Page {page}: {len(found)} structures detected")
        except Exception as exc:
            st.error(f"Page {page} failed: {type(exc).__name__}: {exc}")
            with st.expander(f"Technical traceback for page {page}"):
                st.code(traceback.format_exc(), language="text")
        progress.progress((i + 1) / len(pages), text=f"Processed {i + 1}/{len(pages)} pages")
    st.session_state.extractions = all_records
    st.session_state.last_selection = pages
    if not all_records:
        st.warning("No structures extracted. Check page quality and server logs.")
    else:
        st.success(f"Extracted {len(all_records)} structures from pages {pages}.")

records = st.session_state.extractions
if records:
    st.subheader(f"Results · {len(records)} structures")
    export_rows = []
    for n, rec in enumerate(records):
        with st.expander(f"Page {rec['page']} · structure {rec['ordinal']}", expanded=n < 3):
            a, b, c = st.columns([1, 2, 1])
            with a:
                st.image(rec["image"], caption="Detected structure", use_container_width=True)
            with b:
                rec["smiles"] = st.text_area("Review/edit predicted SMILES",
                    rec["smiles"], key=f"smiles_{key}_{rec['page']}_{rec['ordinal']}")
                check, normalized = syntax_check(rec["smiles"])
                st.write(check)
                if rec["error"]:
                    st.warning(f"Recognition error: {rec['error']}")
            with c:
                mol = Chem.MolFromSmiles(rec["smiles"]) if normalized else None
                if mol:
                    try:
                        from rdkit.Chem import rdMolDraw2D
                        drawer = rdMolDraw2D.MolDraw2DSVG(300, 220)
                        drawer.DrawMolecule(mol)
                        drawer.FinishDrawing()
                        st.image(drawer.GetDrawingText(), caption="RDKit redraw")
                    except (ImportError, OSError) as exc:
                        st.caption(f"RDKit drawing unavailable on this server: {exc}")
                else:
                    st.caption("No redraw available")
        check, canonical = syntax_check(rec["smiles"])
        export_rows.append({
            "pdf": upload.name, "page": rec["page"], "structure": rec["ordinal"],
            "bbox_y0_x0_y1_x1": rec["bbox"], "predicted_smiles": rec["original_smiles"],
            "reviewed_smiles": rec["smiles"], "canonical_smiles": canonical,
            "syntax_check": check, "recognition_error": rec["error"]
        })

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(export_rows[0]))
    writer.writeheader()
    writer.writerows(export_rows)
    st.download_button("Download results CSV", buffer.getvalue(), "panChem_results.csv", mime="text/csv")
    lines = "\n".join(f"{r['reviewed_smiles']}\tpage{r['page']}_structure{r['structure']}"
                      for r in export_rows if r["canonical_smiles"])
    st.download_button("Download valid SMILES (.smi)", lines, "panChem_structures.smi")
    archive = io.BytesIO()
    with ZipFile(archive, "w") as z:
        z.writestr("results.csv", buffer.getvalue())
        z.writestr("structures.smi", lines)
        for rec in records:
            z.writestr(f"images/page_{rec['page']}_structure_{rec['ordinal']}.png", rec["image"])
    st.download_button("Download CSV + SMILES + images ZIP", archive.getvalue(),
                       "panChem_extraction.zip", mime="application/zip")
st.caption("DECIMER prediction is not chemical verification. Inspect stereochemistry, substituents and Markush labels before use.")
