"""Fast chemical regression checks for panChem scaffold builder."""
from rdkit import Chem
from scaffold_builder import assemble, build_records, export_csv, export_sdf, export_smi, AssemblyError

core = "Cc1nc([*:1])[nH]c1[*:2]"
mol = assemble(core, {1: "c1ccccc1", 2: "c1ccc(O)cc1"})
assert Chem.MolToSmiles(mol) == Chem.MolToSmiles(Chem.MolFromSmiles("Cc1nc(-c2ccccc2)[nH]c1-c1ccc(O)cc1"))
assert Chem.MolToSmiles(assemble("C([*:1])[*:2]", {1: "O", 2: "N"})) == "NCO"

for scaffold, assignments in [
    (core, {1: "c1ccccc1"}),
    (core, {1: "c1ccccc1", 2: "bad%%%"}),
    ("C([*:1])[*:1]", {1: "O"}),
    ("C([*:1])[*:2]", {1: "O", 2: "[*:2]N"}),
]:
    try:
        assemble(scaffold, assignments)
    except (AssemblyError, ValueError):
        pass
    else:
        raise AssertionError(f"Accepted invalid chemistry: {scaffold}, {assignments}")

records = [
    {"Compound_ID": "2a", "Template": "core", "R1": "phenyl",
     "R2": "hydroxyphenyl", "Source": "Article", "Page": "4"},
    {"Compound_ID": "2b", "Template": "core", "R1": "phenyl", "R2": "invalid-thing"},
    {"Compound_ID": "2a", "Template": "core", "R1": "phenyl", "R2": "hydroxyphenyl"},
]
rows, molecules = build_records(records, {"core": core}, {
    "phenyl": "c1ccccc1", "hydroxyphenyl": "c1ccc(O)cc1"})
assert [r["Status"] for r in rows] == ["VALID_SYNTAX", "FAILED", "FAILED"]
assert len(molecules) == 1 and rows[0]["Source"] == "Article"
assert b"2a" in export_csv(rows)
assert export_smi(rows).count(b"\n") == 1
sdf = export_sdf(molecules).decode()
assert "$$$$" in sdf and "2a" in sdf
supplier = Chem.SDMolSupplier()
supplier.SetData(sdf, removeHs=True)
assert len([m for m in supplier if m is not None]) == 1
print("Scaffold assembly, source metadata, failures and CSV/SMI/SDF exports: PASS")
