"""Chemical review and provenance utilities for panChem."""
import csv
import io
import json
import re
import zipfile
from datetime import datetime, timezone

from rdkit import Chem
from rdkit.Chem import inchi, rdMolDraw2D

MARKUSH = re.compile(r"\[(?:R\d*|X\d*|Y\d*|Z\d*|Ar\d*)\]|\bR\d+\b", re.IGNORECASE)

def assess(smiles):
    s = (smiles or "").strip()
    markush = bool(MARKUSH.search(s))
    mol = Chem.MolFromSmiles(s) if s and not markush else None
    if markush:
        status = "MARKUSH_REVIEW"
    elif mol is None:
        status = "INVALID_SMILES" if s else "MISSING_SMILES"
    else:
        status = "VALID_SYNTAX"
    canonical = Chem.MolToSmiles(mol, isomericSmiles=True) if mol else ""
    try:
        key = inchi.MolToInchiKey(mol) if mol else ""
    except Exception:
        key = ""
    return status, canonical, key, mol

def molecule_svg(mol):
    d = rdMolDraw2D.MolDraw2DSVG(460, 330)
    d.DrawMolecule(mol)
    d.FinishDrawing()
    return d.GetDrawingText()

def rows_from_artifact(blob):
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        csv_names = [n for n in z.namelist() if n.endswith("results.csv")]
        if not csv_names:
            raise ValueError("results.csv was not found in artifact")
        rows = list(csv.DictReader(io.StringIO(z.read(csv_names[0]).decode("utf-8-sig"))))
        images = {name: z.read(name) for name in z.namelist() if name.lower().endswith(".png")}
        return rows, images

def make_review_zip(rows, images):
    buf = io.BytesIO()
    output = []
    now = datetime.now(timezone.utc).isoformat()
    for row in rows:
        rec = dict(row)
        rec["reviewed_at_utc"] = now
        rec.pop("_mol", None)
        output.append(rec)
    text = io.StringIO()
    if output:
        w = csv.DictWriter(text, fieldnames=list(output[0]))
        w.writeheader()
        w.writerows(output)
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("reviewed_results.csv", text.getvalue())
        z.writestr("reviewed_results.json", json.dumps(output, indent=2, ensure_ascii=False))
        z.writestr("reviewed.smi", "\n".join(
            f"{r['canonical_smiles']} page{r.get('page')}_structure{r.get('structure')}"
            for r in output if r.get("qa_status") == "VALID_SYNTAX"
        ))
        for name, data in images.items():
            z.writestr("original_images/" + name.rsplit("/",1)[-1], data)
    return buf.getvalue()
