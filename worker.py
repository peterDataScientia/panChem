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
from progress_events import publish
from performance_diagnostics import PerformanceRecorder

def parse_pages(raw, count):
    if raw.strip().lower() == 'all':
        return list(range(1, count + 1))
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
    perf = PerformanceRecorder()
    import_started = perf.now()
    from decimer_segmentation import segment_chemical_structures_from_file
    from DECIMER import predict_SMILES
    perf.model_import_seconds = perf.elapsed(import_started)

    outdir.mkdir(parents=True,exist_ok=True)
    pdf_path=outdir/"input.pdf"
    download_started = perf.now()
    fetch_pdf(url,pdf_path)
    perf.pdf_fetch_seconds = perf.elapsed(download_started)
    pdf=pdfium.PdfDocument(str(pdf_path))
    selected=parse_pages(pages,len(pdf))
    rows=[]
    publish("extracting", total_pages=len(selected), completed_pages=0, structures=0)
    for completed_pages, page in enumerate(selected, 1):
        print(f"Processing page {page}",flush=True)
        render_started = perf.now()
        pil=pdf[page-1].render(scale=300/72).to_pil().convert("RGB")
        page_image=outdir/f"page_{page}.png"
        pil.save(page_image)
        render_seconds = perf.elapsed(render_started)
        segment_started = perf.now()
        segments,boxes=segment_chemical_structures_from_file(str(page_image),expand=True,return_bboxes=True)
        segmentation_seconds = perf.elapsed(segment_started)
        page_image.unlink(missing_ok=True)
        recognition_seconds = 0.0
        valid_smiles = 0
        for index,(segment,box) in enumerate(zip(segments,boxes),1):
            image_path=outdir/f"page_{page}_structure_{index}.png"
            Image.fromarray(segment).convert("RGB").save(image_path)
            recognition_started = perf.now()
            try:
                smiles=str(predict_SMILES(str(image_path)))
                mol=Chem.MolFromSmiles(smiles)
                canonical=Chem.MolToSmiles(mol) if mol else ""
                error=""
            except Exception as exc:
                smiles=canonical=""
                error=f"{type(exc).__name__}: {exc}"
            recognition_seconds += perf.elapsed(recognition_started)
            valid_smiles += bool(canonical)
            rows.append(dict(page=page,structure=index,bbox=str(tuple(map(int,box))),
                             smiles=smiles,canonical_smiles=canonical,error=error,
                             image=image_path.name))
        perf.add_page(page, render_seconds, segmentation_seconds,
                      recognition_seconds, len(segments), valid_smiles)
        print(f"Page {page}: {len(segments)} structures",flush=True)
        publish("page_complete", page=page, total_pages=len(selected),
                completed_pages=completed_pages, page_structures=len(segments), structures=len(rows))
    pdf.close()
    pdf_path.unlink(missing_ok=True)
    with (outdir/"results.csv").open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=["page","structure","bbox","smiles","canonical_smiles","error","image"])
        w.writeheader();w.writerows(rows)
    (outdir/"results.smi").write_text("\n".join(f"{r['canonical_smiles']} page{r['page']}_{r['structure']}" for r in rows if r["canonical_smiles"]),encoding="utf-8")
    perf.save(outdir)
    print(f"Completed. Structures: {len(rows)}",flush=True)

if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--url",default="")
    ap.add_argument("--file",default="")
    ap.add_argument("--pages",required=True)
    ap.add_argument("--output",default="results")
    a=ap.parse_args()
    if a.file:
        source = Path(a.file).resolve()
        base = Path.cwd().resolve()
        if not source.is_relative_to(base / "jobs") or not source.is_file() or source.suffix.lower() != ".pdf":
            raise ValueError("Input PDF must be inside jobs/ directory")
        def local_run(path, pages, outdir):
            perf = PerformanceRecorder()
            import_started = perf.now()
            from decimer_segmentation import segment_chemical_structures_from_file
            from DECIMER import predict_SMILES
            perf.model_import_seconds = perf.elapsed(import_started)
            outdir.mkdir(parents=True, exist_ok=True)
            pdf = pdfium.PdfDocument(str(path))
            selected = parse_pages(pages, len(pdf))
            rows = []
            publish("extracting", total_pages=len(selected), completed_pages=0, structures=0)
            for completed_pages, page in enumerate(selected, 1):
                render_started = perf.now()
                pil = pdf[page-1].render(scale=300/72).to_pil().convert("RGB")
                page_img = outdir / f"page_{page}.png"
                pil.save(page_img)
                render_seconds = perf.elapsed(render_started)
                segment_started = perf.now()
                segments, boxes = segment_chemical_structures_from_file(str(page_img), expand=True, return_bboxes=True)
                segmentation_seconds = perf.elapsed(segment_started)
                page_img.unlink(missing_ok=True)
                recognition_seconds = 0.0
                valid_smiles = 0
                for idx, (seg, box) in enumerate(zip(segments,boxes),1):
                    image = outdir / f"page_{page}_structure_{idx}.png"
                    Image.fromarray(seg).convert("RGB").save(image)
                    recognition_started = perf.now()
                    try:
                        smiles = str(predict_SMILES(str(image)))
                        mol = Chem.MolFromSmiles(smiles)
                        canonical = Chem.MolToSmiles(mol) if mol else ""
                        err = ""
                    except Exception as exc:
                        smiles = canonical = ""
                        err = f"{type(exc).__name__}: {exc}"
                    recognition_seconds += perf.elapsed(recognition_started)
                    valid_smiles += bool(canonical)
                    rows.append(dict(page=page, structure=idx, bbox=str(tuple(map(int,box))),
                                     smiles=smiles, canonical_smiles=canonical, error=err, image=image.name))
                perf.add_page(page, render_seconds, segmentation_seconds,
                              recognition_seconds, len(segments), valid_smiles)
                print(f"Page {page}: {len(segments)} detected structures", flush=True)
                publish("page_complete", page=page, total_pages=len(selected),
                        completed_pages=completed_pages, page_structures=len(segments), structures=len(rows))
            pdf.close()
            with (outdir/"results.csv").open("w", newline="",encoding="utf-8") as output:
                writer=csv.DictWriter(output,fieldnames=["page","structure","bbox","smiles","canonical_smiles","error","image"])
                writer.writeheader()
                writer.writerows(rows)
            (outdir/"results.smi").write_text("\n".join(f"{r['canonical_smiles']} page{r['page']}_{r['structure']}" for r in rows if r["canonical_smiles"]),encoding="utf-8")
            perf.save(outdir)
            print(f"Completed {len(rows)} structures",flush=True)
        local_run(source,a.pages,Path(a.output))
    elif a.url:
        run(a.url,a.pages,Path(a.output))
    else:
        raise ValueError("Provide --file or --url")
