# panChem

Automatic PDF-to-SMILES extraction in Streamlit using **DECIMER Image Segmentation** and **DECIMER Image Transformer**.

## Features
- Upload PDF and choose pages using a list/range such as `2,5,8-10`.
- Preview any PDF page.
- Detect **all** chemical structure depictions on each chosen page, without manual cropping.
- Run DECIMER recognition for each detected image.
- Visually compare detection with RDKit redraw and edit predicted SMILES.
- Export CSV, SMI and ZIP with detected images and page/bounding-box provenance.

## Running
Recommended: Python 3.10 or 3.11.

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Streamlit Cloud
Repository `peterDataScientia/panChem`, branch `main`, main file `app.py`. DECIMER includes substantial TensorFlow model downloads; free hosting may run out of memory. Prefer a more capable runtime or a GPU service for heavy PDFs.

## Limitations
This version detects all structures on **selected pages**, and handles failed pages independently. A valid SMILES string may still differ chemically from the figure. Complex Markush R groups may need separate enumeration and verification. No manual crop is required.
