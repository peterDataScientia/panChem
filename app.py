import csv
import io
import hashlib
import tempfile
import zipfile

import pypdfium2 as pdfium
import streamlit as st
from PIL import Image
from streamlit_image_coordinates import streamlit_image_coordinates

st.set_page_config(page_title="panChem | PDF to SMILES", page_icon="🧪", layout="wide")
st.title("🧪 panChem")
st.caption("Select a paper page, crop a chemical structure, predict SMILES with DECIMER, then review and export.")

@st.cache_data(show_spinner=False, max_entries=12)
def render_page(data: bytes, page: int, scale: float = 2.0):
    doc = pdfium.PdfDocument(data)
    try:
        return doc[page - 1].render(scale=scale).to_pil().convert("RGB")
    finally:
        doc.close()

@st.cache_resource(show_spinner="Loading DECIMER model...")
def get_predictor():
    from DECIMER import predict_SMILES
    return predict_SMILES

def predict(image):
    model = get_predictor()
    with tempfile.NamedTemporaryFile(suffix=".png") as f:
        image.save(f.name)
        return str(model(f.name))

def validate(smiles):
    from rdkit import Chem
    mol = Chem.MolFromSmiles(smiles)
    return ("Valid syntax", Chem.MolToSmiles(mol)) if mol else ("Invalid syntax", smiles)

upload = st.file_uploader("Upload PDF research paper", type="pdf")
if upload is None:
    st.info("Upload a PDF to start.")
    st.stop()

data = upload.getvalue()
if len(data) > 25 * 1024 * 1024:
    st.error("PDF exceeds 25 MB limit.")
    st.stop()

fingerprint = hashlib.sha256(data).hexdigest()[:16]
if st.session_state.get("active_pdf") != fingerprint:
    st.session_state.active_pdf = fingerprint
    st.session_state.results = []
    st.session_state.corners = []
    st.session_state.last_click = None
    st.session_state.crop_key = None

try:
    doc = pdfium.PdfDocument(data)
    count = len(doc)
    doc.close()
except Exception as exc:
    st.error(f"Could not read PDF: {exc}")
    st.stop()

st.success(f"Loaded {count} pages")
page = st.selectbox("Page to extract from", range(1, count + 1), format_func=lambda n: f"Page {n}")
image = render_page(data, page)
width = st.slider("Preview width", 400, 1100, 750, step=50)
ratio = width / image.width
preview = image.resize((width, round(image.height * ratio)))

st.subheader("Select structure")
st.write("Click top-left corner and then bottom-right corner around a structure.")
selection_id = f"{fingerprint}:{page}:{width}"
if st.session_state.crop_key != selection_id:
    st.session_state.crop_key = selection_id
    st.session_state.corners = []
    st.session_state.last_click = None

clicked = streamlit_image_coordinates(preview, key=selection_id)
if clicked:
    pos = (clicked["x"], clicked["y"])
    if pos != st.session_state.last_click:
        st.session_state.last_click = pos
        if len(st.session_state.corners) == 2:
            st.session_state.corners = []
        st.session_state.corners.append(pos)

if st.button("Reset crop"):
    st.session_state.corners = []
    st.session_state.last_click = None
    st.rerun()

corners = st.session_state.corners
st.caption(f"Crop corners: {len(corners)}/2")
if len(corners) == 2:
    (x1, y1), (x2, y2) = corners
    x0, x3 = sorted((round(x1 / ratio), round(x2 / ratio)))
    y0, y3 = sorted((round(y1 / ratio), round(y2 / ratio)))
    if x3 - x0 < 10 or y3 - y0 < 10:
        st.warning("Select a larger region.")
    else:
        crop = image.crop((x0, y0, x3, y3))
        st.image(crop, caption="Selected structure", width=350)
        if st.button("Recognize with DECIMER", type="primary"):
            try:
                with st.spinner("Predicting SMILES (model download on first run may be large)..."):
                    raw = predict(crop)
                    status, canonical = validate(raw)
                records = st.session_state.results
                records.append(dict(pdf=upload.name, page=page, structure_id=len(records) + 1,
                                    smiles=raw, canonical_smiles=canonical,
                                    syntax_check=status, image=crop.copy()))
                st.success(f"Structure saved. {status}. Confirm chemical correctness manually.")
            except Exception as exc:
                st.error(f"DECIMER inference failed: {exc}")

records = st.session_state.results
if records:
    st.subheader("Review and export")
    for i, item in enumerate(records):
        with st.expander(f"Structure {item['structure_id']} · page {item['page']}"):
            col1, col2 = st.columns([1, 2])
            with col1:
                st.image(item["image"], use_container_width=True)
            with col2:
                item["smiles"] = st.text_input("Review/edit SMILES", item["smiles"], key=f"smiles_{fingerprint}_{i}")
                item["syntax_check"], item["canonical_smiles"] = validate(item["smiles"])
                st.caption(f"{item['syntax_check']} · valid SMILES syntax does not confirm correct recognition")
    fields = ["pdf", "page", "structure_id", "smiles", "canonical_smiles", "syntax_check"]
    csv_file = io.StringIO()
    writer = csv.DictWriter(csv_file, fieldnames=fields)
    writer.writeheader()
    writer.writerows({k: entry[k] for k in fields} for entry in records)
    st.download_button("Download CSV", csv_file.getvalue(), "panChem_results.csv", "text/csv")
    smi = "\n".join(f"{entry['smiles']}\tpage{entry['page']}_structure{entry['structure_id']}" for entry in records)
    st.download_button("Download SMILES (.smi)", smi, "panChem_results.smi", "text/plain")
    bundle = io.BytesIO()
    with zipfile.ZipFile(bundle, "w") as zf:
        zf.writestr("results.csv", csv_file.getvalue())
        for entry in records:
            image_bytes = io.BytesIO()
            entry["image"].save(image_bytes, "PNG")
            zf.writestr(f"page{entry['page']}_structure{entry['structure_id']}.png", image_bytes.getvalue())
    st.download_button("Download CSV + images (ZIP)", bundle.getvalue(), "panChem_results.zip", "application/zip")
    if st.button("Clear extracted results"):
        st.session_state.results = []
        st.rerun()

st.caption("MVP: manual cropping. Automatic page-wide segmentation and Markush enumeration are not included.")
