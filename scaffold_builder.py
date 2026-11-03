"""Scaffold/R-group assembly and exports for panChem.

Manual transcription is required; RDKit validation does not prove that a
chemical structure matches the source publication.
"""
from __future__ import annotations
import csv
import io

from rdkit import Chem
from rdkit.Chem import Descriptors, rdMolDescriptors, rdDepictor


class AssemblyError(ValueError):
    pass


def assemble(scaffold_smiles: str, fragments: dict[int, str]):
    """Join one attachment-first fragment per numbered scaffold dummy atom."""
    scaffold = Chem.MolFromSmiles(str(scaffold_smiles).strip())
    if scaffold is None:
        raise AssemblyError("Invalid scaffold SMILES.")
    slots = []
    for atom in scaffold.GetAtoms():
        if atom.GetAtomicNum() == 0:
            n = atom.GetAtomMapNum()
            if n < 1 or atom.GetDegree() != 1:
                raise AssemblyError("Each scaffold attachment must be a numbered [*:n] with one bond.")
            slots.append(n)
    if not slots or len(slots) != len(set(slots)):
        raise AssemblyError("Scaffold needs unique numbered attachment points.")
    if set(slots) != set(fragments):
        raise AssemblyError(f"Scaffold needs slots {sorted(slots)}; supplied {sorted(fragments)}.")
    combined = scaffold
    for slot in sorted(slots):
        fragment = str(fragments[slot]).strip()
        if not fragment:
            raise AssemblyError(f"R{slot} is empty.")
        if fragment.startswith("*") or "[*:" in fragment:
            raise AssemblyError(f"R{slot}: enter an attachment-first fragment without a dummy atom.")
        # The numbered dummy is joined to the first atom in the entered fragment.
        part = Chem.MolFromSmiles(f"[*:{slot}]{fragment}")
        if part is None:
            raise AssemblyError(f"Invalid SMILES at R{slot}: {fragment}")
        dummy = next((a for a in part.GetAtoms() if a.GetAtomicNum() == 0 and a.GetAtomMapNum() == slot), None)
        if dummy is None or dummy.GetDegree() != 1:
            raise AssemblyError(f"R{slot} must start at a single attachment atom.")
        combined = Chem.CombineMols(combined, part)
    try:
        molecule = Chem.molzip(combined)
        Chem.SanitizeMol(molecule)
    except Exception as exc:
        raise AssemblyError(f"Could not assemble structure: {exc}") from exc
    if any(a.GetAtomicNum() == 0 for a in molecule.GetAtoms()):
        raise AssemblyError("Some attachment points remain unresolved.")
    return molecule


def build_records(compounds, templates, library):
    """Return (rows, molecules) with transparent per-compound errors."""
    rows = []
    molecules = []
    seen = set()
    for record in compounds:
        cid = str(record.get("Compound_ID") or "").strip()
        if not cid:
            continue
        template = str(record.get("Template") or "").strip()
        row = {
            "Compound_ID": cid, "Template": template,
            "Source": str(record.get("Source") or "").strip(),
            "Page": str(record.get("Page") or "").strip(),
            "SMILES": "", "Formula": "", "MW": "", "InChIKey": "",
            "Status": "FAILED", "Error": "",
        }
        try:
            if cid in seen:
                raise AssemblyError("Duplicate compound identifier.")
            seen.add(cid)
            if template not in templates:
                raise AssemblyError(f"Unknown scaffold: {template}")
            assignments = {}
            for slot in (1, 2, 3):
                raw = str(record.get(f"R{slot}") or "").strip()
                if raw:
                    assignments[slot] = library.get(raw, raw)
            mol = assemble(templates[template], assignments)
            row["SMILES"] = Chem.MolToSmiles(mol, isomericSmiles=True)
            row["Formula"] = rdMolDescriptors.CalcMolFormula(mol)
            row["MW"] = round(Descriptors.MolWt(mol), 2)
            try:
                row["InChIKey"] = Chem.MolToInchiKey(mol)
            except Exception:
                pass
            row["Status"] = "VALID_SYNTAX"
            molecules.append((cid, mol))
        except Exception as exc:
            row["Error"] = str(exc)
        rows.append(row)
    return rows, molecules


def export_csv(rows):
    if not rows:
        return b""
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def export_smi(rows):
    return ("".join(
        f"{r['SMILES']}\t{r['Compound_ID']}\n"
        for r in rows if r["Status"] == "VALID_SYNTAX"
    )).encode("utf-8")


def export_sdf(molecules):
    stream = io.StringIO()
    writer = Chem.SDWriter(stream)
    for cid, mol in molecules:
        m = Chem.Mol(mol)
        rdDepictor.Compute2DCoords(m)
        m.SetProp("_Name", cid)
        m.SetProp("SMILES", Chem.MolToSmiles(mol, isomericSmiles=True))
        writer.write(m)
    writer.close()
    return stream.getvalue().encode("utf-8")
