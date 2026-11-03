"""Automatic curation of DECIMER results and scaffold-family grouping.

Automatic grouping only handles fully specified, chemically valid structures.
Markush scaffold diagrams and textual R-group lookup tables require explicit
matching/review; no unsupported chemical assignments are inferred.
"""
from __future__ import annotations

import csv
import io
import zipfile

from rdkit import Chem
from rdkit.Chem.Scaffolds import MurckoScaffold
from chemqa import assess


def candidate_rows(extracted):
    """Normalize DECIMER/QA rows without silently accepting ambiguous structures."""
    output = []
    for i, record in enumerate(extracted, start=1):
        raw = str(record.get("reviewed_smiles") or record.get("smiles") or "").strip()
        status, canonical, key, mol = assess(raw)
        if mol is not None and any(atom.GetAtomicNum() == 0 for atom in mol.GetAtoms()):
            status, canonical, key, mol = "MARKUSH_REVIEW", "", "", None
        scaffold = ""
        if mol is not None:
            try:
                scaf = MurckoScaffold.GetScaffoldForMol(mol)
                scaffold = Chem.MolToSmiles(scaf, isomericSmiles=True) if scaf.GetNumAtoms() else ""
            except Exception:
                pass
        output.append({
            "Compound_ID": str(record.get("Compound_ID") or f"page{record.get('page', '?')}_structure{record.get('structure', i)}"),
            "Page": str(record.get("page") or record.get("Page") or ""),
            "Structure": str(record.get("structure") or i),
            "Original_SMILES": raw, "SMILES": canonical,
            "InChIKey": key, "Scaffold_SMILES": scaffold,
            "Status": status,
            "Review": "Check source figure and table before publication" if status == "VALID_SYNTAX" else "Manual identification required",
            "Source": str(record.get("Source") or ""),
        })
    return output


def read_decimer_zip(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        matches = [name for name in archive.namelist() if name.endswith("results.csv") or name.endswith("reviewed_results.csv")]
        if not matches:
            raise ValueError("No DECIMER results.csv or reviewed_results.csv found.")
        preferred = next((name for name in matches if name.endswith("reviewed_results.csv")), matches[0])
        return list(csv.DictReader(io.StringIO(archive.read(preferred).decode("utf-8-sig"))))


def read_decimer_csv(data):
    return list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))


def export_candidates_csv(rows):
    if not rows:
        return b""
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def export_candidates_smi(rows):
    return "".join(
        f"{row['SMILES']}\t{row['Compound_ID']}\n"
        for row in rows if row["Status"] == "VALID_SYNTAX" and row["SMILES"]
    ).encode("utf-8")
