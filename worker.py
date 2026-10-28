"""GitHub Actions DECIMER worker for selected pages of research PDFs.

Preserves the original 300 DPI segmentation, printed DECIMER model and outputs.
Optimizations:
- Overlap loading the printed recognition model with first-page segmentation.
- Pass identical RGB pixels as NumPy arrays to DECIMER (avoid PNG re-reading).
- Reuse inference for pixel-identical depictions within one PDF job.
Set PANCHEM_PARALLEL_MODEL_PRELOAD=0 to disable background model loading.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import csv
import hashlib
import os
from pathlib import Path
from urllib.parse import urlparse

import numpy as np
import requests
import pypdfium2 as pdfium
from PIL import Image
from rdkit import Chem

from progress_events import publish
from performance_diagnostics import PerformanceRecorder


def parse_pages(raw, count):
    if raw.strip().lower() == "all":
        return list(range(1, count + 1))
    out = set()
    for item in raw.replace(" ", "").split(","):
        if "-" in item:
            x, y = map(int, item.split("-"))
            if x > y:
                raise ValueError("Descending page range")
            out.update(range(x, y + 1))
        elif item:
            out.add(int(item))
    if not out or min(out) < 1 or max(out) > count:
        raise ValueError("Page selection out of range")
    return sorted(out)


def fetch_pdf(url, destination):
    u = urlparse(url)
    if u.scheme != "https" or not u.hostname or u.username or u.password:
        raise ValueError("HTTPS URL is required")
    # Untrusted PDFs are downloaded on an isolated GitHub Actions runner.
    with requests.get(url, timeout=(15, 120), stream=True, allow_redirects=True) as response:
        response.raise_for_status()
        if urlparse(response.url).scheme != "https":
            raise ValueError("Redirect must remain HTTPS")
        size = 0
        with destination.open("wb") as stream:
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > 25 * 1024 * 1024:
                    raise ValueError("PDF must be <=25 MB")
                stream.write(chunk)
    with destination.open("rb") as stream:
        if stream.read(5) != b"%PDF-":
            raise ValueError("Not a PDF")


def _pixel_key(image):
    """Exact RGB pixel identity; no lossy perceptual deduplication."""
    digest = hashlib.blake2b(digest_size=20)
    digest.update(str(image.shape).encode("ascii"))
    digest.update(image.tobytes())
    return digest.digest()


def _recognize(image, predict_smiles, cache, key):
    if key in cache:
        smiles, canonical = cache[key]
        return smiles, canonical, "", True

    try:
        # DECIMER 2.8.1 explicitly accepts a NumPy RGB array. These are exactly
        # the same pixels saved to the original-image PNG for review.
        smiles = str(predict_smiles(image))
        mol = Chem.MolFromSmiles(smiles)
        canonical = Chem.MolToSmiles(mol) if mol else ""
        error = ""
        # Cache only successful calls (not transient model/network exceptions).
        cache[key] = (smiles, canonical)
    except Exception as exc:
        smiles = canonical = ""
        error = f"{type(exc).__name__}: {exc}"
    return smiles, canonical, error, False


def process_document(pdf_path, pages, outdir, perf):
    from decimer_segmentation import segment_chemical_structures_from_file
    import DECIMER

    outdir.mkdir(parents=True, exist_ok=True)
    doc = pdfium.PdfDocument(str(pdf_path))
    selected = parse_pages(pages, len(doc))
    rows = []
    prediction_cache = {}
    parallel_preload = os.environ.get("PANCHEM_PARALLEL_MODEL_PRELOAD", "1").lower() not in (
        "0", "false", "off", "no"
    )
    perf.recognition_preload_enabled = parallel_preload
    executor = None
    preload = None
    preload_checked = False

    def load_printed_model():
        begin = perf.now()
        DECIMER.load_models(printed=True, hand_drawn=False)
        return perf.elapsed(begin)

    publish("extracting", total_pages=len(selected), completed_pages=0, structures=0)

    try:
        for completed_pages, page in enumerate(selected, 1):
            print(f"Processing page {page}", flush=True)
            begin = perf.now()
            pil = doc[page - 1].render(scale=300 / 72).to_pil().convert("RGB")
            page_image = outdir / f"page_{page}.png"
            pil.save(page_image)
            render_seconds = perf.elapsed(begin)

            # Begin recognition model loading just before segmentation to
            # overlap two independent stages. No hand-drawn model is loaded.
            if parallel_preload and preload is None:
                executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="decimer-preload")
                preload = executor.submit(load_printed_model)

            begin = perf.now()
            try:
                segments, boxes = segment_chemical_structures_from_file(
                    str(page_image), expand=True, return_bboxes=True
                )
            finally:
                page_image.unlink(missing_ok=True)
            segmentation_seconds = perf.elapsed(begin)

            recognition_seconds = 0.0
            valid_smiles = 0
            cache_hits = 0
            for index, (segment, box) in enumerate(zip(segments, boxes), 1):
                image_path = outdir / f"page_{page}_structure_{index}.png"
                # Normalize just once, retaining the original exported PNG.
                rgb_image = Image.fromarray(segment).convert("RGB")
                rgb_image.save(image_path)
                image_array = np.asarray(rgb_image)

                key = _pixel_key(image_array)
                if preload is not None and not preload_checked and key not in prediction_cache:
                    begin_wait = perf.now()
                    try:
                        perf.recognition_preload_seconds = preload.result()
                    except Exception as exc:
                        # Fall back to DECIMER's existing lazy-loading behavior.
                        print(f"[PERF] Recognition preloading failed ({type(exc).__name__}); retrying lazily", flush=True)
                    perf.recognition_preload_wait_seconds = perf.elapsed(begin_wait)
                    preload_checked = True

                started = perf.now()
                smiles, canonical, error, cached = _recognize(
                    image_array, DECIMER.predict_SMILES, prediction_cache, key
                )
                seconds = perf.elapsed(started)
                recognition_seconds += seconds
                valid_smiles += bool(canonical)
                cache_hits += int(cached)
                perf.add_recognition(page, index, seconds, cached)

                rows.append(dict(
                    page=page, structure=index, bbox=str(tuple(map(int, box))),
                    smiles=smiles, canonical_smiles=canonical, error=error,
                    image=image_path.name
                ))

            perf.add_page(
                page, render_seconds, segmentation_seconds,
                recognition_seconds, len(segments), valid_smiles,
                cache_hits=cache_hits
            )
            print(f"Page {page}: {len(segments)} detected structures", flush=True)
            publish(
                "page_complete", page=page, total_pages=len(selected),
                completed_pages=completed_pages,
                page_structures=len(segments), structures=len(rows)
            )
    finally:
        doc.close()
        if executor is not None:
            executor.shutdown(wait=True)
        # If the document has no detected structures, there is no recognition.
        if preload is not None and not preload_checked and preload.done():
            try:
                perf.recognition_preload_seconds = preload.result()
            except Exception:
                pass

    with (outdir / "results.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "page", "structure", "bbox", "smiles",
                "canonical_smiles", "error", "image"
            ]
        )
        writer.writeheader()
        writer.writerows(rows)
    (outdir / "results.smi").write_text(
        "\n".join(
            f"{record['canonical_smiles']} page{record['page']}_{record['structure']}"
            for record in rows if record["canonical_smiles"]
        ),
        encoding="utf-8"
    )
    perf.save(outdir)
    print(f"Completed {len(rows)} structures", flush=True)


def run(url, pages, outdir):
    perf = PerformanceRecorder()
    begin = perf.now()
    from decimer_segmentation import segment_chemical_structures_from_file  # noqa: F401
    import DECIMER  # noqa: F401
    perf.model_import_seconds = perf.elapsed(begin)
    outdir.mkdir(parents=True, exist_ok=True)
    pdf_path = outdir / "input.pdf"
    begin = perf.now()
    fetch_pdf(url, pdf_path)
    perf.pdf_fetch_seconds = perf.elapsed(begin)
    try:
        process_document(pdf_path, pages, outdir, perf)
    finally:
        pdf_path.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="")
    parser.add_argument("--file", default="")
    parser.add_argument("--pages", required=True)
    parser.add_argument("--output", default="results")
    args = parser.parse_args()
    if args.file:
        source = Path(args.file).resolve()
        base = Path.cwd().resolve()
        if (not source.is_relative_to(base / "jobs")
                or not source.is_file() or source.suffix.lower() != ".pdf"):
            raise ValueError("Input PDF must be inside jobs/ directory")
        perf = PerformanceRecorder()
        begin = perf.now()
        from decimer_segmentation import segment_chemical_structures_from_file  # noqa: F401
        import DECIMER  # noqa: F401
        perf.model_import_seconds = perf.elapsed(begin)
        process_document(source, args.pages, Path(args.output), perf)
    elif args.url:
        run(args.url, args.pages, Path(args.output))
    else:
        raise ValueError("Provide --file or --url")


if __name__ == "__main__":
    main()
