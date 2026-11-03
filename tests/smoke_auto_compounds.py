"""Regression checks for DECIMER-to-compound automatic curation."""
import csv
import io
import zipfile
from auto_compounds import candidate_rows, read_decimer_zip, read_decimer_csv, export_candidates_csv, export_candidates_smi

data = [
    {"page": "4", "structure": "1", "smiles": "c1ccccc1O"},
    {"page": "4", "structure": "2", "smiles": "Oc1ccc(Cl)cc1"},
    {"page": "4", "structure": "3", "smiles": "not_a_molecule"},
    {"page": "5", "structure": "1", "smiles": "R1"},
]
rows = candidate_rows(data)
assert len(rows) == 4
assert [row["Status"] for row in rows] == [
    "VALID_SYNTAX", "VALID_SYNTAX", "INVALID_SMILES", "MARKUSH_REVIEW"
]
assert rows[0]["Scaffold_SMILES"] == "c1ccccc1"
assert rows[0]["Page"] == "4"
assert len([r for r in rows if r["Scaffold_SMILES"] == "c1ccccc1"]) == 2
assert export_candidates_smi(rows).count(b"\n") == 2
assert b"MARKUSH_REVIEW" in export_candidates_csv(rows)

stream = io.StringIO()
writer = csv.DictWriter(stream, fieldnames=["page", "structure", "smiles"])
writer.writeheader()
writer.writerows(data)
blob = stream.getvalue().encode()
assert len(read_decimer_csv(blob)) == 4
zip_bytes = io.BytesIO()
with zipfile.ZipFile(zip_bytes, "w") as z:
    z.writestr("results.csv", blob)
assert len(read_decimer_zip(zip_bytes.getvalue())) == 4
rejected = candidate_rows([{"smiles": "CCO", "reviewed_smiles": "", "page": "2", "structure": "1"}])
assert rejected[0]["Status"] == "MISSING_SMILES", "Rejected recognition must not reappear"
# Regression inputs based on observed panChem page-4 DECIMER output.
# Disconnected components and attachment-like radicals must not be exported.
problematic = candidate_rows([
    {"page": "4", "structure": "3",
     "smiles": "[CH]C1=C2C=CNC2=NC=C1.[CH]C1=C2C=CNC2=NC=C1"},
    {"page": "4", "structure": "6",
     "smiles": "CC1=C2C(=CNC2=NC=C1)C.CC1=CC=NC=C1"},
    {"page": "4", "structure": "1",
     "smiles": "C1=CC=C(C=C1)N2CCC3=C(C=C(C=C3)[R1])CC2=O"},
])
assert all(row["Status"] != "VALID_SYNTAX" for row in problematic)
assert export_candidates_smi(problematic) == b""
print("DECIMER CSV/ZIP, scaffold grouping, ambiguous entry exclusion, export: PASS")
