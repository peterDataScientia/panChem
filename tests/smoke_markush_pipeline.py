"""Regression tests: DECIMER Markush scaffold and PDF SAR table assembly."""
from rdkit import Chem
from markush_pipeline import (
    normalize_scaffold, resolve_group, complete, extract_tables, build_from_table,
    export_constructed_smi, export_constructed_sdf,
)

source = "C1=CC=C(C=C1)N2CCC3=C(C=C(C=C3)[R1])CC2=O"
scaffold = normalize_scaffold(source)
assert scaffold and "[*:1]" in scaffold
assert normalize_scaffold("Cc1ccccc1") is None
assert normalize_scaffold("c1ccc([R1])cc1[R1]") is None

table = extract_tables({4: "Table 1\nCompound  R1  R2  IC50\n8a  Cl  OMe  0.8\n8b  F  H  1.1\n"})
assert len(table) == 2
assert table[0]["R_groups"] == {1:"Cl",2:"OMe"}

single = extract_tables({4: "Compound   R1   IC50\n8a   Cl   2.2\n8b   H   3.4\n"})
assert len(single) == 2
results = build_from_table(scaffold, single, "scaffold A")
assert len(results) == 2
assert all(r["Status"] == "CONSTRUCTED_CANDIDATE" for r in results)
for row in results:
    mol = Chem.MolFromSmiles(row["SMILES"])
    assert mol and not any(a.GetAtomicNum()==0 for a in mol.GetAtoms())
assert "Cl" in results[0]["SMILES"]
assert "Cl" not in results[1]["SMILES"]
assert export_constructed_smi(results).count(b"\n") == 2
sdf = export_constructed_sdf(results).decode("utf-8")
supplier = Chem.SDMolSupplier()
supplier.SetData(sdf, removeHs=True)
assert len([m for m in supplier if m is not None]) == 2
assert "CONSTRUCTED_CANDIDATE_NOT_SOURCE_VERIFIED" in sdf

refused = build_from_table(scaffold, [
    {"Compound_ID":"X1","Page":4,"R_groups":{1:"unknown R group"}},
    {"Compound_ID":"X2","Page":4,"R_groups":{2:"Cl"}},
])
assert all(r["Status"] == "NEEDS_REVIEW" for r in refused)
assert resolve_group("4-Cl-Ph") == "c1ccc(Cl)cc1"
print("Markush normalization, table rows, chemistry assembly, uncertain mapping: PASS")
