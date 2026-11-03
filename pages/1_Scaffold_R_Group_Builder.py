"""panChem literature compound-series builder; complements DECIMER extraction."""
import io

import pandas as pd
import pypdfium2 as pdfium
import streamlit as st

from chemqa import molecule_svg
from pdf_viewer import full_page_viewer
from scaffold_builder import build_records, export_csv, export_sdf, export_smi
from auto_compounds import candidate_rows, read_decimer_zip, read_decimer_csv, export_candidates_csv, export_candidates_smi

st.set_page_config(page_title="panChem | Scaffold builder", page_icon="🧪", layout="wide")
st.title("🧪 panChem")
st.caption("Extract and curate chemical structures from research PDFs.")
st.subheader("Scaffold + R-group builder")
st.caption("Use the same source-document review workflow as panChem DECIMER, with manual scaffold definitions and batch R-group assembly.")

st.subheader("1. Auto-extract compounds from DECIMER")
st.caption("Use the latest panChem extraction automatically, or import a DECIMER results ZIP/CSV. Complete SMILES are grouped by RDKit Murcko scaffold; unresolved Markush labels are held for review.")
extracted = st.session_state.get("panchem_builder_source_rows", [])
source_file = st.file_uploader("Or import DECIMER results (.zip or .csv)", type=["zip", "csv"], key="pc_auto_source")
if source_file:
    try:
        extracted = (read_decimer_zip(source_file.getvalue()) if source_file.name.lower().endswith(".zip")
                     else read_decimer_csv(source_file.getvalue()))
    except Exception as exc:
        st.error(f"Cannot read DECIMER results: {exc}")
        extracted = []
if extracted:
    st.caption(f"{len(extracted)} source records available.")
    if st.button("Generate compound set automatically", type="primary", key="pc_auto_run"):
        st.session_state.pc_auto_results = candidate_rows(extracted)
if "pc_auto_results" in st.session_state:
    auto_rows = st.session_state.pc_auto_results
    valid_auto = [r for r in auto_rows if r["Status"] == "VALID_SYNTAX"]
    unresolved = [r for r in auto_rows if r["Status"] != "VALID_SYNTAX"]
    aa, bb, cc = st.columns(3)
    aa.metric("Detected", len(auto_rows))
    bb.metric("Valid candidates", len(valid_auto))
    cc.metric("Need review", len(unresolved))
    st.dataframe(pd.DataFrame(auto_rows), hide_index=True, width="stretch")
    scaffold_groups = {}
    for rec in valid_auto:
        scaffold_groups.setdefault(rec["Scaffold_SMILES"] or "(no ring scaffold)", []).append(rec["Compound_ID"])
    with st.expander("Detected scaffold families", expanded=True):
        for scaffold, members in sorted(scaffold_groups.items(), key=lambda item: -len(item[1])):
            st.code(scaffold, language=None)
            st.caption(f"{len(members)} compound(s): {', '.join(members[:20])}")
    st.warning("Murcko scaffold grouping does not reconstruct compounds from Markush R-group tables. Unresolved entries are excluded from SMI export; check extracted structures against the PDF.")
    c1, c2 = st.columns(2)
    c1.download_button("Download candidate CSV", export_candidates_csv(auto_rows), "panChem_auto_compounds.csv",
                       "text/csv", use_container_width=True)
    c2.download_button("Download valid SMI", export_candidates_smi(auto_rows), "panChem_auto_compounds.smi",
                       "text/plain", use_container_width=True, disabled=not valid_auto)
st.divider()
st.subheader("Manual completion — only when needed")
with st.expander("2. Inspect source PDF", expanded=True):
    upload = st.file_uploader("Upload paper or supplementary PDF", type=["pdf"], key="pc_scaffold_pdf")
    if upload:
        data = upload.getvalue()
        if len(data) > 25 * 1024 * 1024:
            st.error("Maximum PDF size is 25 MB.")
        else:
            try:
                doc = pdfium.PdfDocument(data)
                try:
                    n_pages = len(doc)
                    if n_pages < 1:
                        raise ValueError("PDF contains no pages.")
                    st.caption(f"{n_pages} page(s). This viewer does not send the file to GitHub.")
                    st.components.v1.html(full_page_viewer(data), height=760, scrolling=False)
                finally:
                    doc.close()
            except Exception as exc:
                st.error(f"Could not preview PDF: {exc}")
    else:
        st.info("Upload a PDF to inspect structures. You can also build structures without uploading a file.")

st.subheader("3. Scaffolds")
st.caption("Place numbered attachment atoms at substitution sites, e.g. [*:1], [*:2], [*:3].")
if "pc_scaffold_templates" not in st.session_state:
    st.session_state.pc_scaffold_templates = pd.DataFrame([
        {"Template": "example", "Scaffold_SMILES": "Cc1nc([*:1])[nH]c1[*:2]"}])
if "pc_scaffold_fragments" not in st.session_state:
    st.session_state.pc_scaffold_fragments = pd.DataFrame([
        {"Name": "phenyl", "SMILES": "c1ccccc1"},
        {"Name": "4-hydroxyphenyl", "SMILES": "c1ccc(O)cc1"}])
if "pc_scaffold_compounds" not in st.session_state:
    st.session_state.pc_scaffold_compounds = pd.DataFrame([
        {"Compound_ID": "Example-1", "Template": "example",
         "R1": "phenyl", "R2": "4-hydroxyphenyl", "R3": "",
         "Source": "", "Page": ""}])
templates = st.data_editor(st.session_state.pc_scaffold_templates, num_rows="dynamic",
                           width="stretch", key="pc_templates_editor")
st.subheader("4. Fragment library")
st.caption("Each fragment SMILES starts at its attachment atom. Example: para-hydroxyphenyl is c1ccc(O)cc1.")
fragments = st.data_editor(st.session_state.pc_scaffold_fragments, num_rows="dynamic",
                           width="stretch", key="pc_fragments_editor")
st.subheader("5. Compound assignments")
st.caption("Fill R1–R3 by fragment name or SMILES. Add source and page for traceability.")
compounds = st.data_editor(st.session_state.pc_scaffold_compounds, num_rows="dynamic",
                           width="stretch", key="pc_compounds_editor")
run, reset = st.columns([2, 1])
clicked = run.button("Build and validate structures", type="primary", use_container_width=True)
if reset.button("Clear results", use_container_width=True):
    st.session_state.pop("pc_scaffold_results", None)
    st.session_state.pop("pc_scaffold_molecules", None)
    st.rerun()
if clicked:
    names = [str(r.get("Template") or "").strip() for r in templates.fillna("").to_dict("records") if str(r.get("Template") or "").strip()]
    fragment_names = [str(r.get("Name") or "").strip() for r in fragments.fillna("").to_dict("records") if str(r.get("Name") or "").strip()]
    if len(names) != len(set(names)) or len(fragment_names) != len(set(fragment_names)):
        st.error("Scaffold and fragment names must be unique.")
    else:
        templates_map = {str(r["Template"]).strip(): str(r["Scaffold_SMILES"]).strip()
                         for r in templates.fillna("").to_dict("records") if str(r["Template"]).strip()}
        library_map = {str(r["Name"]).strip(): str(r["SMILES"]).strip()
                       for r in fragments.fillna("").to_dict("records") if str(r["Name"]).strip()}
        rows, molecules = build_records(compounds.fillna("").to_dict("records"), templates_map, library_map)
        st.session_state.pc_scaffold_results = rows
        st.session_state.pc_scaffold_molecules = molecules

if "pc_scaffold_results" in st.session_state:
    st.divider()
    st.subheader("6. Review and export")
    rows = st.session_state.pc_scaffold_results
    molecules = st.session_state.pc_scaffold_molecules
    if not rows:
        st.warning("No compound identifiers supplied.")
    else:
        valid = sum(r["Status"] == "VALID_SYNTAX" for r in rows)
        a, b, c = st.columns(3)
        a.metric("Compounds", len(rows))
        b.metric("SMILES valid", valid)
        c.metric("Failed", len(rows) - valid)
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        if molecules:
            st.markdown("**Molecular structure review**")
            for start in range(0, len(molecules), 3):
                columns = st.columns(3)
                for col, (cid, mol) in zip(columns, molecules[start:start+3]):
                    with col:
                        st.caption(cid)
                        svg = molecule_svg(mol)
                        if svg:
                            st.image(svg, use_container_width=True)
                        else:
                            st.warning("2D preview unavailable.")
        st.warning("RDKit validates structure syntax and valence, not correspondence with the published drawing, attachment positions or stereochemistry. Verify against the PDF.")
        left, mid, right = st.columns(3)
        left.download_button("Download CSV", export_csv(rows), "panChem_scaffold_compounds.csv",
                             "text/csv", use_container_width=True)
        mid.download_button("Download SMI", export_smi(rows), "panChem_scaffold_compounds.smi",
                            "text/plain", use_container_width=True, disabled=not valid)
        right.download_button("Download SDF", export_sdf(molecules) if molecules else b"",
                              "panChem_scaffold_compounds.sdf", "chemical/x-mdl-sdfile",
                              use_container_width=True, disabled=not valid)
