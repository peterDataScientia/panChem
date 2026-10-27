# panChem

Streamlit PDF chemical-structure recognition app powered by DECIMER.

## Features
- Upload PDF (up to 25 MB) and select a numbered page.
- Draw a crop by clicking its top-left and bottom-right corners.
- Predict SMILES via DECIMER, validate SMILES syntax using RDKit, and manually edit results.
- Export CSV, SMI, and cropped structure images in a ZIP.

## Run
Python 3.10 or 3.11 recommended.

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Deploy
On Streamlit Community Cloud, select `peterDataScientia/panChem`, branch `main`, entrypoint `app.py`. DECIMER includes a large model and may exceed free-tier disk/RAM constraints. In that case use a more capable host or dedicated inference backend.

## Limitations
Automatic page-wide structure detection and Markush R-group enumeration are **not** implemented in this MVP. SMILES syntax validation is not proof of chemical correctness. Verify all chemical structures visually before research use.
