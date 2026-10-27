"""GitHub Actions DECIMER worker for URL-hosted research PDFs."""
import argparse
import csv
import io
import os
from pathlib import Path
from urllib.parse import urlparse
from zipfile import ZipFile

import requests
import pypdfium2 as pdfium
from PIL import Image
from rdkit import Chem

def parse_pages(raw, count):
    out=set()
    for item in raw.replace(" ","").split(","):
        if "-" in item:
            x,y=map(int,item.split("-"))
            if x>y: raise ValueError("Descending page range")
            out.update(range(x,y+1))
        elif item: out.add(int(item))
    if not out or min(out)<1 or max(out)>count: raise ValueError("Page selection out of range")
    return sorted(out)

def fetch_pdf(url, destination):
    u=urlparse(url)
    if u.scheme!="https" or not u.hostname or u.username or u.password:
        raise ValueError("HTTPS URL is required")
    # Trusted URL only: untrusted external PDFs are downloaded on an isolated CI runner.
    with requests.get(url,timeout=(15,120),stream=True,allow_redirects=True) as response:
        response.raise_for_status()
        if urlparse(response.url).scheme != "https":
            raise ValueError("Redirect must remain HTTPS")
        size=0
        with destination.open("wb") as stream:
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size>25*1024*1024: raise ValueError("PDF must be <=25 MB")
                stream.write(chunk)
    with destination.open("rb") as stream:
        if stream.read(5)!=b"%PDF-": raise ValueError("Not a PDF")

def run(url,pages,outdir):
    from decimer_segmentation import segment_chemical_structures_from_file
    from DECIMER import predict_SMILES

    outdir.mkdir(parents=True,exist_ok=True)
    pdf_path=outdir/"input.pdf"
    fetch_pdf(url,pdf_path)
    pdf=pdfium.PdfDocument(str(pdf_path))
    selected=parse_pages(pages,len(pdf))
    rows=[]
    for page in selected:
        print(f"Processing page {page}",flush=True)
        pil=pdf[page-1].render(scale=300/72).to_pil().convert("RGB")
        page_image=outdir/f"page_{page}.png"
        pil.save(page_image)
        segments,boxes=segment_chemical_structures_from_file(str(page_image),expand=True,return_bboxes=True)
        page_image.unlink(missing_ok=True)
        for index,(segment,box) in enumerate(zip(segments,boxes),1):
            image_path=outdir/f"page_{page}_structure_{index}.png"
            Image.fromarray(segment).convert("RGB").save(image_path)
            try:
                smiles=str(predict_SMILES(str(image_path)))
                mol=Chem.MolFromSmiles(smiles)
                canonical=Chem.MolToSmiles(mol) if mol else ""
                error=""
            except Exception as exc:
                smiles=canonical=""
                error=f"{type(exc).__name__}: {exc}"
            rows.append(dict(page=page,structure=index,bbox=str(tuple(map(int,box))),
                             smiles=smiles,canonical_smiles=canonical,error=error,
                             image=image_path.name))
        print(f"Page {page}: {len(segments)} structures",flush=True)
    pdf.close()
    pdf_path.unlink(missing_ok=True)
    with (outdir/"results.csv").open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=["page","structure","bbox","smiles","canonical_smiles","error","image"])
        w.writeheader();w.writerows(rows)
    (outdir/"results.smi").write_text("\n".join(f"{r['canonical_smiles']} page{r['page']}_{r['structure']}" for r in rows if r["canonical_smiles"]),encoding="utf-8")
    print(f"Completed. Structures: {len(rows)}",flush=True)

if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--url",required=True)
    ap.add_argument("--pages",required=True)
    ap.add_argument("--output",default="results")
    a=ap.parse_args()
    run(a.url,a.pages,Path(a.output))
