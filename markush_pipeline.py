"""PDF text-layer SAR tables and conservative Markush structure assembly.

No OCR dependency or unsupported structural inference. Image-only tables remain
unresolved. Designed for Python 3.11, pypdfium2, and RDKit already in panChem.
"""
from __future__ import annotations
import re
from rdkit import Chem
from rdkit.Chem import Descriptors, rdMolDescriptors

LABEL = re.compile(r"\[(?:R|r)([1-3])\]")
HEADER = re.compile(r"(?i)^(?:compound|compd\.?|entry|no\.?|id)\b")
R_COLUMNS = re.compile(r"(?i)^R[1-3]$")
KNOWN = {
    "H": None, "-H": None, "HYDROGEN": None,
    "F": "F", "CL": "Cl", "BR": "Br", "I": "I",
    "ME": "C", "CH3": "C", "ET": "CC", "C2H5": "CC",
    "OH": "O", "HO": "O", "OMe": "OC", "OCH3": "OC",
    "OEt": "OCC", "OC2H5": "OCC",
    "NH2": "N", "NHME": "NC", "NME2": "N(C)C",
    "CN": "C#N", "NO2": "[N+](=O)[O-]",
    "CF3": "C(F)(F)F", "CHF2": "C(F)F",
    "Ph": "c1ccccc1", "phenyl": "c1ccccc1",
    "4-Cl-Ph": "c1ccc(Cl)cc1", "4-F-Ph": "c1ccc(F)cc1",
    "4-OMe-Ph": "c1ccc(OC)cc1",
}
KNOWN_UPPER = {k.upper().replace(" ", ""): v for k,v in KNOWN.items()}


def normalize_scaffold(smiles):
    """Convert DECIMER [R1] placeholders to molzip attachment markers."""
    raw = str(smiles or "").strip()
    if not LABEL.search(raw):
        return None
    candidate = LABEL.sub(lambda match: "[*:" + match.group(1) + "]", raw)
    molecule = Chem.MolFromSmiles(candidate)
    if molecule is None:
        return None
    mapped = [a.GetAtomMapNum() for a in molecule.GetAtoms() if a.GetAtomicNum() == 0]
    if not mapped or any(n not in (1,2,3) for n in mapped) or len(mapped) != len(set(mapped)):
        return None
    if any(a.GetDegree()!=1 for a in molecule.GetAtoms() if a.GetAtomicNum()==0):
        return None
    return Chem.MolToSmiles(molecule, isomericSmiles=True)


def resolve_group(value):
    """Return attachment-first SMILES, None for H, or raise for ambiguity."""
    raw = str(value or "").strip().replace("−", "-").replace("–", "-")
    if not raw:
        raise ValueError("Empty R-group value")
    key = raw.upper().replace(" ", "")
    if key in KNOWN_UPPER:
        return KNOWN_UPPER[key]
    if raw.startswith("SMILES:"):
        raw = raw[7:].strip()
    else:
        raise ValueError("Unmapped substituent: " + raw)
    if not raw or "." in raw or "*" in raw:
        raise ValueError("Invalid explicit fragment")
    molecule = Chem.MolFromSmiles(raw)
    if molecule is None:
        raise ValueError("Invalid explicit fragment")
    return Chem.MolToSmiles(molecule, isomericSmiles=True) if molecule.GetNumAtoms()==1 else raw


def complete(scaffold, assignments):
    """Assemble a unique molecule; never infer attachment directions."""
    molecule = Chem.MolFromSmiles(scaffold)
    if molecule is None:
        raise ValueError("Invalid normalized scaffold")
    slots = [a.GetAtomMapNum() for a in molecule.GetAtoms() if a.GetAtomicNum()==0]
    if not slots or len(set(slots)) != len(slots) or set(slots) != set(assignments):
        raise ValueError("Attachment positions do not match table columns")
    combined = molecule
    hydrogen_slots = set()
    for slot in slots:
        substituent = resolve_group(assignments[slot])
        if substituent is None:
            hydrogen_slots.add(slot)
            continue
        frag = Chem.MolFromSmiles(f"[*:{slot}]{substituent}")
        if frag is None:
            raise ValueError("Invalid R-group chemistry at R" + str(slot))
        combined = Chem.CombineMols(combined,frag)
    joined = Chem.molzip(combined)
    if hydrogen_slots:
        editable = Chem.RWMol(joined)
        remove = [a.GetIdx() for a in joined.GetAtoms()
                  if a.GetAtomicNum()==0 and a.GetAtomMapNum() in hydrogen_slots]
        if len(remove)!=len(hydrogen_slots):
            raise ValueError("Hydrogen replacement lost its attachment")
        for idx in sorted(remove,reverse=True):
            editable.RemoveAtom(idx)
        joined = editable.GetMol()
    Chem.SanitizeMol(joined)
    if any(a.GetAtomicNum()==0 for a in joined.GetAtoms()):
        raise ValueError("Unresolved attachment atoms")
    if len(Chem.GetMolFrags(joined)) != 1:
        raise ValueError("Product has disconnected components")
    return joined


def page_text(pdf_bytes, pages=None):
    """Read existing PDF text layers; no OCR or cloud uploads."""
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(pdf_bytes)
    try:
        selected = range(1,len(doc)+1) if pages is None else pages
        result = {}
        for page_num in selected:
            if page_num < 1 or page_num > len(doc):
                continue
            page = doc[page_num - 1]
            textpage = page.get_textpage()
            try:
                result[page_num] = textpage.get_text_range()
            finally:
                textpage.close()
        return result
    finally:
        doc.close()


def _tokens(line):
    text = line.strip().replace("│","  ").replace("|","  ")
    if "\t" in text:
        cells=[x.strip() for x in text.split("\t") if x.strip()]
        if len(cells)>1:
            return cells
    cells=[x.strip() for x in re.split(r"\s{2,}",text) if x.strip()]
    return cells if len(cells)>1 else text.split()


def extract_tables(pages):
    """Extract candidate compound/R-group rows from readable text tables.

    Header must include Compound/Entry/ID and at least one R1/R2/R3 field.
    Rows without a full set of column values remain unparsed, not guessed.
    """
    results=[]
    for page,text in pages.items():
        headers=None
        for line in text.splitlines():
            tokens=_tokens(line)
            normalized=[re.sub(r"[^A-Za-z0-9]","",x).upper() for x in tokens]
            if len(tokens)>=2 and HEADER.match(tokens[0]) and any(R_COLUMNS.fullmatch(t) for t in normalized[1:]):
                headers=normalized
                continue
            if headers is None:
                continue
            if not line.strip():
                continue
            if len(tokens) != len(headers):
                if re.match(r"^(?:Table|Figure|Scheme|Notes|References)\b", line.strip(),re.I):
                    headers=None
                continue
            compound=tokens[0].strip()
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,30}",compound):
                continue
            groups={int(h[1:]):v for h,v in zip(headers,tokens) if R_COLUMNS.fullmatch(h)}
            if groups:
                results.append({"Compound_ID":compound,"Page":page,"R_groups":groups,
                                "Raw_row":line.strip()})
    return results


def build_from_table(scaffold, rows, scaffold_id=""):
    """Construct full compounds with provenance and individual failure reasons."""
    slots=sorted(int(x) for x in re.findall(r"\[\*:(\d+)\]",scaffold))
    results=[]
    for row in rows:
        entry={"Compound_ID":row["Compound_ID"],"Page":row.get("Page",""),
               "Scaffold_ID":scaffold_id,"R_groups":row.get("R_groups",{}),
               "SMILES":"","Formula":"","MW":"","Status":"NEEDS_REVIEW","Reason":""}
        try:
            group=row["R_groups"]
            if set(group)!=set(slots):
                raise ValueError("Table R columns do not match scaffold sites")
            mol=complete(scaffold,group)
            entry.update(SMILES=Chem.MolToSmiles(mol,isomericSmiles=True),
                         Formula=rdMolDescriptors.CalcMolFormula(mol),
                         MW=round(Descriptors.MolWt(mol),2),
                         Status="CONSTRUCTED_CANDIDATE")
        except Exception as exc:
            entry["Reason"]=str(exc)
        results.append(entry)
    return results


def export_constructed_sdf(rows):
    """Export chemically constructible candidates with explicit review metadata."""
    from rdkit.Chem import rdDepictor
    import io
    stream = io.StringIO()
    writer = Chem.SDWriter(stream)
    for row in rows:
        if row.get("Status") != "CONSTRUCTED_CANDIDATE":
            continue
        mol = Chem.MolFromSmiles(row.get("SMILES", ""))
        if mol is None:
            continue
        mol.SetProp("_Name", str(row.get("Compound_ID", "")))
        mol.SetProp("Validation_Status", "CONSTRUCTED_CANDIDATE_NOT_SOURCE_VERIFIED")
        mol.SetProp("Source_Page", str(row.get("Page", "")))
        mol.SetProp("Scaffold_ID", str(row.get("Scaffold_ID", "")))
        rdDepictor.Compute2DCoords(mol)
        writer.write(mol)
    writer.close()
    return stream.getvalue().encode("utf-8")


def export_constructed_smi(rows):
    return "".join(
        f"{r['SMILES']}\t{r['Compound_ID']}\n"
        for r in rows if r.get("Status") == "CONSTRUCTED_CANDIDATE" and r.get("SMILES")
    ).encode("utf-8")


def extract_tables_from_pdf(pdf_bytes, focus_pages=None, max_ocr_pages=8):
    """Read text tables and optionally OCR image-backed tables locally.

    Does not use cloud APIs. OCR is restricted to selected/focus pages, and
    an image-only table that fails parsing stays unresolved. A row is
    never fabricated from a chemical drawing alone.
    """
    text = page_text(pdf_bytes)
    rows = extract_tables(text)
    if not focus_pages:
        focus_pages = list(text)
    focus = set()
    for n in focus_pages:
        for page_number in (n - 1, n, n + 1):
            if page_number in text:
                focus.add(page_number)
    pending = [p for p in sorted(focus) if not any(r["Page"] == p for r in rows)]
    pending = pending[:max(0, max_ocr_pages)]
    if not pending:
        return rows, {"pages": len(text), "ocr_pages": [], "ocr_available": True}
    try:
        import pytesseract
        import pypdfium2 as pdfium
        import shutil
        if not shutil.which("tesseract"):
            return rows, {"pages":len(text),"ocr_pages":[],"ocr_available":False}
    except ImportError:
        return rows, {"pages":len(text),"ocr_pages":[],"ocr_available":False}
    doc = pdfium.PdfDocument(pdf_bytes)
    scanned = []
    try:
        for page_number in pending:
            page = doc[page_number - 1]
            try:
                image = page.render(scale=2.5).to_pil().convert("RGB")
                ocr_text = pytesseract.image_to_string(
                    image, config="--psm 6", timeout=40
                )
            except Exception:
                continue
            scanned.append(page_number)
            rows.extend(extract_tables({page_number: ocr_text}))
    finally:
        doc.close()
    unique = {}
    for row in rows:
        key = (row["Page"], row["Compound_ID"], tuple(sorted(row["R_groups"].items())))
        unique.setdefault(key,row)
    return list(unique.values()), {
        "pages":len(text), "ocr_pages":scanned, "ocr_available":True
    }
