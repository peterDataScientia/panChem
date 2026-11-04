"""panChem connected Markush reconstruction.

Uses DECIMER session results and the original uploaded PDF. Markush molecular
assembly is based on table rows, not Bemis-Murcko scaffolds.
"""
from __future__ import annotations

import hashlib
import json

import pandas as pd
import streamlit as st
from rdkit import Chem

from auto_compounds import candidate_rows, export_candidates_csv, export_candidates_smi
from chemqa import molecule_svg
from markush_pipeline import (
    normalize_scaffold, extract_tables_from_pdf, build_from_table,
    export_constructed_smi, export_constructed_sdf,
)
from pdf_viewer import full_page_viewer
from source_pdf import resolve_pdf_for_run

st.set_page_config(page_title="panChem | Markush reconstruction", page_icon="🧪", layout="wide")
st.title("🧪 panChem")
st.caption("DECIMER → Markush scaffold → R-group table → complete molecular structures")
st.subheader("Markush and R-group reconstruction")

source = st.session_state.get("panchem_builder_source_rows") or []
pdf_bytes = st.session_state.get("panchem_source_pdf")
active_run = st.session_state.get("active_run_id")
active_artifact = st.session_state.get("active_artifact_id")
zip_blob = st.session_state.get(f"artifact-{active_artifact}") if active_artifact else None
source_run = st.session_state.get("panchem_source_pdf_run_id")
if pdf_bytes and active_run and source_run and str(source_run) != str(active_run):
    pdf_bytes = None
if not pdf_bytes:
    try:
        import requests
        def _request(method, url, token):
            response = requests.request(
                method, url,
                headers={"Authorization": f"Bearer {token}",
                         "Accept": "application/vnd.github+json",
                         "X-GitHub-Api-Version": "2022-11-28"},
                timeout=60)
            response.raise_for_status()
            return response
        def _token():
            try:
                return st.secrets.get("GITHUB_TOKEN", "")
            except Exception:
                return ""
        @st.cache_data(show_spinner=False, max_entries=12)
        def _restore_pdf(run_id, artifact, token):
            return resolve_pdf_for_run(
                run_id, token, _request,
                "https://api.github.com/repos/peterDataScientia/panChem",
                artifact=artifact)
        pdf_bytes, recovered_job, source_location = _restore_pdf(
            active_run, zip_blob, _token())
        if pdf_bytes:
            st.session_state["panchem_source_pdf"] = pdf_bytes
            st.session_state["panchem_source_pdf_run_id"] = active_run
            if recovered_job:
                st.session_state["panchem_source_job_id"] = recovered_job
        else:
            st.caption(f"Source PDF lookup: {source_location}")
    except Exception:
        st.warning("Could not retrieve the source PDF for this run. Check the matching GitHub job or artifact.")

if not source:
    st.info("Extract a PDF on the main panChem page first. DECIMER results will appear here automatically; no results upload is needed.")
    if st.button("Open PDF extraction", type="primary"):
        st.switch_page("app.py")
    st.stop()

st.caption(f"{len(source)} DECIMER structures received directly from panChem.")

@st.cache_data(show_spinner=False, max_entries=6)
def read_tables(data, focus):
    return extract_tables_from_pdf(data, focus_pages=focus)

def _source_smiles(row):
    return str(row.get("reviewed_smiles") if "reviewed_smiles" in row else row.get("smiles") or "").strip()

candidates = candidate_rows(source)
raw_scaffolds = []
for row in source:
    raw = _source_smiles(row)
    normalized = normalize_scaffold(raw)
    if normalized or "[R" in raw or "[*:" in raw:
        raw_scaffolds.append({
            "Label": f"Page {row.get('page', '?')} · structure {row.get('structure', '?')}",
            "Detected_SMILES": raw,
            "Normalized_SMILES": normalized or "",
        })

pages = tuple(sorted({int(row["page"]) for row in source if str(row.get("page", "")).isdigit()}))
table_rows = []
diagnostics = {}
if pdf_bytes:
    try:
        table_rows, diagnostics = read_tables(pdf_bytes, pages)
    except Exception as exc:
        st.warning(f"Could not read the source PDF table: {exc}")

col1, col2, col3 = st.columns(3)
col1.metric("DECIMER structures", len(source))
col2.metric("Markush candidates", len(raw_scaffolds))
col3.metric("Detected R-group rows", len(table_rows))

tab_complete, tab_markush, tab_other = st.tabs([
    "Constructed molecules", "Markush & R-group review", "Other extracted structures"
])

with tab_markush:
    st.markdown("**Markush scaffolds detected from DECIMER**")
    if raw_scaffolds:
        st.dataframe(pd.DataFrame(raw_scaffolds), hide_index=True, width="stretch")
    else:
        st.warning("No usable numbered Markush labels were recognized automatically. Check the original drawing, then enter its scaffold with [*:1], [*:2], etc. in the correction field below.")

    default_index = 0
    selected = st.selectbox(
        "Scaffold associated with the table",
        options=list(range(len(raw_scaffolds))) if raw_scaffolds else [-1],
        format_func=lambda i: raw_scaffolds[i]["Label"] if i >= 0 else "Enter scaffold SMILES",
        key="pc_markush_selected",
    )
    default_scaffold = raw_scaffolds[selected]["Normalized_SMILES"] if selected >= 0 else ""
    if selected >= 0 and not default_scaffold:
        default_scaffold = raw_scaffolds[selected]["Detected_SMILES"]

    st.caption("Attachment sites must be [*:1], [*:2] or [*:3]. Edit only if the DECIMER scaffold contains a recognition error.")
    scaffold_text = st.text_area(
        "Scaffold SMILES",
        value=default_scaffold,
        key=f"pc_markush_scaffold_{selected}",
        height=85,
    )
    normalized = normalize_scaffold(scaffold_text)
    if not normalized and "[*:" in scaffold_text:
        mol = Chem.MolFromSmiles(scaffold_text)
        if mol and any(a.GetAtomicNum() == 0 and a.GetAtomMapNum() > 0 for a in mol.GetAtoms()):
            normalized = Chem.MolToSmiles(mol, isomericSmiles=True)
    if normalized:
        mol = Chem.MolFromSmiles(normalized)
        if mol:
            img = molecule_svg(mol)
            if img:
                st.image(img, width=360)
    elif scaffold_text:
        st.error("A usable scaffold was not resolved. Check R labels and attachment atoms before assembly.")

    st.markdown("**R-group table associated with the original PDF**")
    if diagnostics.get("ocr_pages"):
        st.caption("OCR checked pages: " + ", ".join(map(str, diagnostics["ocr_pages"])))
    if pdf_bytes and not table_rows:
        st.info("No machine-readable R-group table was identified. The original PDF is available below; edit only the missing table cells.")
    if not pdf_bytes:
        st.warning("The matching PDF is unavailable in this session. Only the DECIMER structures can be shown; no table assignments will be guessed.")

    source_id = hashlib.sha256(
        (str(st.session_state.get("panchem_source_job_id", "")) + str(len(source))
         + str(table_rows)).encode("utf-8")
    ).hexdigest()[:12]
    editable_rows = [
        {"Compound_ID": row["Compound_ID"], "Page": row.get("Page", ""),
         "R1": row.get("R_groups", {}).get(1, ""),
         "R2": row.get("R_groups", {}).get(2, ""),
         "R3": row.get("R_groups", {}).get(3, "")}
        for row in table_rows
    ]
    edited = st.data_editor(
        pd.DataFrame(editable_rows, columns=["Compound_ID", "Page", "R1", "R2", "R3"]),
        num_rows="dynamic", hide_index=True, width="stretch",
        key=f"pc_markush_table_{source_id}"
    )
    confirmed_rows = []
    for row in edited.fillna("").to_dict("records"):
        cid = str(row.get("Compound_ID", "")).strip()
        if cid:
            confirmed_rows.append({
                "Compound_ID": cid, "Page": row.get("Page", ""),
                "R_groups": {index: str(row.get(f"R{index}", "")).strip()
                             for index in (1, 2, 3) if str(row.get(f"R{index}", "")).strip()}
            })
    st.session_state["pc_markush_active_rows"] = confirmed_rows
    st.session_state["pc_markush_active_scaffold"] = normalized or ""
    st.session_state["pc_markush_active_label"] = (
        raw_scaffolds[selected]["Label"] if selected >= 0 else "Corrected scaffold"
    )

    with st.expander("View original PDF", expanded=False):
        if pdf_bytes:
            st.components.v1.html(full_page_viewer(pdf_bytes), height=700, scrolling=False)
        else:
            st.caption("Original PDF not available in the current session.")

with tab_complete:
    scaffold = st.session_state.get("pc_markush_active_scaffold", "")
    assignment_rows = st.session_state.get("pc_markush_active_rows", [])
    # The Markush tab above executes before this block in Streamlit's script order,
    # including on editor reruns, so the latest edits are reflected immediately.
    if scaffold and assignment_rows:
        results = build_from_table(
            scaffold, assignment_rows,
            st.session_state.get("pc_markush_active_label", "Markush scaffold")
        )
        st.session_state["panchem_constructed_rows"] = results
        completed = [r for r in results if r["Status"] == "CONSTRUCTED_CANDIDATE"]
        metrics = st.columns(3)
        metrics[0].metric("Table compounds", len(results))
        metrics[1].metric("Full structures generated", len(completed))
        metrics[2].metric("Need review", len(results) - len(completed))
        st.dataframe(pd.DataFrame(results), hide_index=True, width="stretch")
        if completed:
            st.markdown("**Completed 2D molecular structures**")
            shown_page = st.number_input(
                "Page of constructed structures", min_value=1,
                max_value=(len(completed)-1)//18+1, value=1, step=1,
                key="pc_constructed_page"
            )
            display = completed[(shown_page-1)*18:shown_page*18]
            for start in range(0, len(display), 3):
                cols = st.columns(3)
                for col, rec in zip(cols, display[start:start+3]):
                    with col:
                        st.caption(f"{rec['Compound_ID']} · {rec['Formula']}")
                        mol = Chem.MolFromSmiles(rec["SMILES"])
                        svg = molecule_svg(mol) if mol else None
                        if svg:
                            st.image(svg, use_container_width=True)
                        st.code(rec["SMILES"], language=None)
        st.caption("These are reconstructed candidate molecules; confirm the scaffold position, substituent assignment and stereochemistry against the PDF.")
        a,b,c = st.columns(3)
        a.download_button("Candidate CSV", pd.DataFrame(results).to_csv(index=False).encode(),
                          "panChem_markush_compounds.csv", "text/csv", use_container_width=True)
        b.download_button("Candidate SMI", export_constructed_smi(results),
                          "panChem_markush_compounds.smi", "text/plain",
                          disabled=not completed, use_container_width=True)
        c.download_button("Candidate SDF", export_constructed_sdf(results),
                          "panChem_markush_compounds.sdf", "chemical/x-mdl-sdfile",
                          disabled=not completed, use_container_width=True)
    else:
        st.info("To construct full molecules, use the Markush & R-group review tab to select or correct a scaffold and its table rows. No chemical structures are fabricated when the original PDF lacks sufficient information.")

with tab_other:
    st.caption("Complete DECIMER predictions are shown separately from reconstructed Markush compounds.")
    valid = [r for r in candidates if r["Status"] == "VALID_SYNTAX"]
    verified = [r for r in valid if r.get("Image_verified") == "YES"]
    a,b,c = st.columns(3)
    a.metric("Predictions", len(candidates))
    b.metric("Syntax valid", len(valid))
    c.metric("Source verified", len(verified))
    st.dataframe(pd.DataFrame(candidates), hide_index=True, width="stretch")
    d1,d2 = st.columns(2)
    d1.download_button("Candidate CSV", export_candidates_csv(candidates),
                       "panChem_decimer_candidates.csv", "text/csv", use_container_width=True)
    d2.download_button("Verified SMI", export_candidates_smi(candidates),
                       "panChem_verified_direct.smi", "text/plain",
                       disabled=not verified, use_container_width=True)
