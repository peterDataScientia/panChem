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


## GitHub Actions backend

Workflow: `.github/workflows/decimer.yml`; worker: `worker.py`.

1. Use **Actions → DECIMER PDF extraction → Run workflow** and enter an HTTPS PDF URL plus page numbers, or use the GitHub Actions section in Streamlit.
2. The Ubuntu runner installs dependencies and downloads the PDF; DECIMER detects structures and predicts SMILES.
3. Download the `panChem-extraction-<run_id>` artifact from the completed workflow, or through the Streamlit job panel.

The Streamlit Actions panel requires a repository-scoped GitHub token in Streamlit Secrets as `GITHUB_TOKEN`, with Actions read/write access. Do not put the token in source code.

**Privacy/security:** Never send confidential/unpublished research PDFs through publicly accessible URLs or public Actions artifacts. Public-repository artifacts and workflow information can be exposed to other users. This first iteration uses externally hosted HTTPS PDF URLs; it does not transfer Streamlit-uploaded PDF bytes to the GitHub runner. Local file upload still uses the local extraction engine. A private storage transfer service is required for secure direct upload-to-Actions support.

Large TensorFlow dependencies and model downloads may cause cold starts. Actual runner success needs verification on a full test PDF.
