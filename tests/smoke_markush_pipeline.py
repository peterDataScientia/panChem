"""Regression tests: DECIMER Markush scaffold and PDF SAR table assembly."""
from rdkit import Chem
from markush_pipeline import (
    normalize_scaffold, resolve_group, complete, extract_tables, build_from_table,
    page_text, extract_tables_from_pdf,
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

# Minimal, self-contained text-layer PDF: verifies the real pdfium parser,
# rather than testing only a synthetic Python string.
def simple_pdf():
    objects = []
    payload = (b"BT /F1 12 Tf 60 750 Td (Compound   R1   IC50) Tj "
               b"0 -20 Td (8a   Cl   0.8) Tj "
               b"0 -20 Td (8b   F   1.1) Tj ET")
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    objects.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                   b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    objects.append(b"<< /Length " + str(len(payload)).encode() + b" >>\nstream\n"
                   + payload + b"\nendstream")
    pdf = b"%PDF-1.4\n"
    offsets = []
    for index, body in enumerate(objects, 1):
        offsets.append(len(pdf))
        pdf += str(index).encode() + b" 0 obj\n" + body + b"\nendobj\n"
    ref = len(pdf)
    pdf += b"xref\n0 6\n0000000000 65535 f \n"
    pdf += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    pdf += (b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n"
            + str(ref).encode() + b"\n%%EOF")
    return pdf

pdf_data = simple_pdf()
assert "Compound" in page_text(pdf_data)[1]
pdf_rows, meta = extract_tables_from_pdf(pdf_data, focus_pages=[1], max_ocr_pages=0)
assert len(pdf_rows) == 2 and pdf_rows[0]["R_groups"] == {1: "Cl"}
assert meta["pages"] == 1

print("Markush normalization, table rows, chemistry assembly, uncertain mapping: PASS")
