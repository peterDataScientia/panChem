# panChem — automated PDF chemical-structure extraction

**Frontend:** Streamlit Community Cloud. **Compute:** GitHub Actions Ubuntu runner. **Models:** Official DECIMER Image Segmentation and Image Transformer.

## Use
1. Deploy this repository on Streamlit Community Cloud; main entry point: `app.py`, Python 3.11.
2. Create a fine-grained GitHub personal access token for **peterDataScientia/panChem** with **Contents: read and write** and **Actions: read and write**.
3. In the Streamlit app dashboard choose **Settings → Secrets** and enter:

```toml
GITHUB_TOKEN = "paste_your_token_here"
```

4. Reboot the app. Upload a PDF (max 25 MB), specify page numbers such as `1,3,5-7`, click **Extract all structures with GitHub Actions**.
5. Open **Job status and downloads**, refresh, retrieve an artifact after completion, view the CSV and download images and SMILES.

## How the compute pipeline works

- Streamlit uploads the PDF as `jobs/<job-id>/paper.pdf` using GitHub's Contents API.
- Streamlit dispatches the `DECIMER PDF extraction` GitHub Actions workflow, passing the path and pages.
- The GitHub runner installs `worker-requirements.txt`, loads the official DECIMER models, segments each selected page and predicts SMILES.
- A downloadable GitHub artifact contains `results.csv`, `results.smi` and source structure PNG crops.

## Limitations and operational details

- Uploaded PDFs are placed in a **public repository** in the current MVP and persist until deleted. Do not upload material you cannot publish.
- This architecture does not need Tailscale, local compute or Streamlit Cloud ML dependencies.
- Model downloads and model initialization are heavy; first runs may take time and exceed available runner resources.
- The automated pipeline has not yet passed a verified complete GitHub-hosted inference smoke test.
- DECIMER cannot be assumed correct for all stereochemistry, abbreviations or Markush tables. Validate figures before using the results in research.
