"""Chemical review and provenance utilities for panChem."""
import csv
import io
import json
import re
import zipfile
from datetime import datetime, timezone

from rdkit import Chem
from rdkit.Chem import rdDepictor
from rdkit.Chem import inchi

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

def _fallback_molecule_svg(mol, width=460, height=330):
    """Render a simplified SVG from RDKit 2D coordinates when Draw extension is unavailable.

    Uses only the RDKit core and Python stdlib; keeps drawing available on
    Streamlit Cloud even if the optional rdMolDraw2D native extension fails.
    """
    import html
    import math
    from rdkit.Chem import rdDepictor

    molecule = Chem.Mol(mol)
    if not molecule.GetNumAtoms():
        return None
    rdDepictor.Compute2DCoords(molecule)
    conf = molecule.GetConformer()
    xy = [(conf.GetAtomPosition(i).x, conf.GetAtomPosition(i).y)
          for i in range(molecule.GetNumAtoms())]
    x_min = min(x for x, _ in xy)
    x_max = max(x for x, _ in xy)
    y_min = min(y for _, y in xy)
    y_max = max(y for _, y in xy)
    span_x = max(x_max - x_min, 1.0)
    span_y = max(y_max - y_min, 1.0)
    scale = min((width - 88) / span_x, (height - 80) / span_y, 66)
    mid_x, mid_y = (x_min + x_max) / 2, (y_min + y_max) / 2
    pts = [(width / 2 + (x - mid_x) * scale,
            height / 2 - (y - mid_y) * scale) for x, y in xy]

    def label(atom):
        symbol = atom.GetSymbol()
        if symbol == "C" and not atom.GetFormalCharge() and not atom.GetIsotope() and molecule.GetNumAtoms() > 1:
            return ""
        if symbol == "*":
            symbol = "R"
        h_count = atom.GetTotalNumHs()
        result = str(atom.GetIsotope()) if atom.GetIsotope() else ""
        result += symbol
        if symbol not in ("C", "R") and h_count:
            result += "H" + (str(h_count) if h_count > 1 else "")
        charge = atom.GetFormalCharge()
        if charge:
            result += ("+" if charge > 0 else "-") + (str(abs(charge)) if abs(charge) > 1 else "")
        return result

    labels = [label(a) for a in molecule.GetAtoms()]
    colors = {"N": "#2256bb", "O": "#c62b35", "S": "#af8011",
              "P": "#bd7711", "F": "#16864a", "Cl": "#16864a",
              "Br": "#8b3c2a", "I": "#693eaa"}
    elements = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-label="2D molecule">',
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="white"/>',
    ]
    for bond in molecule.GetBonds():
        i, j = bond.GetBeginAtomIdx(), bond.GetEndAtomIdx()
        x1, y1 = pts[i]
        x2, y2 = pts[j]
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy)
        if length < 0.001:
            continue
        ux, uy = dx / length, dy / length
        # Keep bonds from running through the atom labels.
        t1 = 12 if labels[i] else 0
        t2 = 12 if labels[j] else 0
        x1, y1 = x1 + ux * t1, y1 + uy * t1
        x2, y2 = x2 - ux * t2, y2 - uy * t2
        nx, ny = -uy, ux

        def line(offset=0, dashed=False):
            dash = ' stroke-dasharray="5,4"' if dashed else ""
            elements.append(
                f'<line x1="{x1 + nx * offset:.2f}" y1="{y1 + ny * offset:.2f}" '
                f'x2="{x2 + nx * offset:.2f}" y2="{y2 + ny * offset:.2f}" '
                f'stroke="#30343b" stroke-width="2" stroke-linecap="round"{dash}/>'
            )

        kind = bond.GetBondType()
        if kind == Chem.BondType.DOUBLE:
            line(-3.2)
            line(3.2)
        elif kind == Chem.BondType.TRIPLE:
            line(-4.7)
            line()
            line(4.7)
        elif kind == Chem.BondType.AROMATIC:
            line()
            line(3.2, dashed=True)
        else:
            line()
    for idx, atom in enumerate(molecule.GetAtoms()):
        txt = labels[idx]
        if not txt:
            continue
        x, y = pts[idx]
        color = colors.get(atom.GetSymbol(), "#30343b")
        # White label backing prevents bonds from crossing the atom symbols.
        box_w = max(23, len(txt) * 10.5 + 10)
        elements.append(
            f'<rect x="{x - box_w / 2:.2f}" y="{y - 13:.2f}" '
            f'width="{box_w:.2f}" height="26" fill="white"/>'
        )
        elements.append(
            f'<text x="{x:.2f}" y="{y + 6:.2f}" text-anchor="middle" '
            f'font-family="Arial, sans-serif" font-size="18" '
            f'font-weight="500" fill="{color}">{html.escape(txt)}</text>'
        )
    elements.append("</svg>")
    return "\n".join(elements)


def molecule_svg(mol):
    """Provide a 2D SVG regardless of native rdMolDraw2D deployment support."""
    if mol is None:
        return None
    try:
        from rdkit.Chem.Draw import rdMolDraw2D
        from rdkit.Chem import rdDepictor

        molecule = Chem.Mol(mol)
        rdDepictor.Compute2DCoords(molecule)
        drawer = rdMolDraw2D.MolDraw2DSVG(460, 330)
        drawer.DrawMolecule(molecule)
        drawer.FinishDrawing()
        svg = drawer.GetDrawingText()
        # st.image accepts SVG markup, not always XML-prefixed SVG files.
        svg_start = svg.find("<svg")
        return svg[svg_start:] if svg_start >= 0 else svg
    except Exception as error:
        import logging
        logging.warning("Native RDKit drawing failed; using SVG fallback: %s", error)
        try:
            return _fallback_molecule_svg(mol)
        except Exception:
            logging.exception("Core RDKit coordinate drawing also failed")
            return None


def rows_from_artifact(blob):
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        csv_names = [n for n in z.namelist() if n.endswith("results.csv")]
        if not csv_names:
            raise ValueError("results.csv was not found in artifact")
        rows = list(csv.DictReader(io.StringIO(z.read(csv_names[0]).decode("utf-8-sig"))))
        images = {name: z.read(name) for name in z.namelist() if name.lower().endswith(".png")}
        return rows, images

def make_review_zip(rows, images, molfiles=None):
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
        combined_sdf = []
        for index, row in enumerate(output):
            if row.get("qa_status") != "VALID_SYNTAX":
                continue
            smiles = row.get("canonical_smiles", "")
            mol = Chem.MolFromSmiles(smiles)
            if mol is None:
                continue
            # Use the hand-drawn Ketcher MOL only if it still matches the
            # reviewed molecule; otherwise create consistent 2D coordinates.
            block = (molfiles or {}).get(str(index), "")
            parsed = Chem.MolFromMolBlock(block, sanitize=True, removeHs=True) if block else None
            if parsed is None or Chem.MolToSmiles(parsed, isomericSmiles=False) != Chem.MolToSmiles(mol, isomericSmiles=False):
                molecule = Chem.Mol(mol)
                rdDepictor.Compute2DCoords(molecule)
                block = Chem.MolToMolBlock(molecule)
            label = f"page{row.get('page')}_structure{row.get('structure')}"
            z.writestr(f"molecules/{label}.mol", block)
            record = block.rstrip() + "\n> <SMILES>\n" + smiles + "\n\n$$$$\n"
            z.writestr(f"molecules/{label}.sdf", record)
            combined_sdf.append(record)
        if combined_sdf:
            z.writestr("reviewed_structures.sdf", "".join(combined_sdf))
        for name, data in images.items():
            z.writestr("original_images/" + name.rsplit("/",1)[-1], data)
    return buf.getvalue()
