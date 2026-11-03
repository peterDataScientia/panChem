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
print("DECIMER CSV/ZIP, scaffold grouping, ambiguous entry exclusion, export: PASS")
