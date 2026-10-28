"""Validate Ketcher import/save, SMILES safety and MOL/SDF exports."""
import io
import zipfile

from rdkit import Chem
from ketcher_review import validated_ketcher_result, initial_molfile, result_molfile
from chemqa import make_review_zip

molblock = initial_molfile("CCO")
assert "V2000" in molblock
assert Chem.MolFromMolBlock(molblock) is not None

value, output_mol = validated_ketcher_result(
    {"action": "apply", "molecule_key": "1:2", "smiles": "CCO", "molfile": molblock},
    "1:2",
)
assert value == "CCO"
assert output_mol == molblock
assert Chem.MolFromMolBlock(result_molfile(value, output_mol)) is not None

for payload in [
    {"action": "apply", "molecule_key": "WRONG", "smiles": "CCO", "molfile": molblock},
    {"action": "apply", "molecule_key": "1:2", "smiles": "CCN", "molfile": molblock},
    {"action": "apply", "molecule_key": "1:2", "smiles": "invalid)", "molfile": molblock},
]:
    try:
        validated_ketcher_result(payload, "1:2")
    except ValueError:
        pass
    else:
        raise AssertionError(f"Unsafe Ketcher output accepted: {payload}")

rows = [dict(page="1", structure="1", canonical_smiles="CCO",
             qa_status="VALID_SYNTAX", reviewed_smiles="CCO")]
blob = make_review_zip(rows, {}, {"0": molblock})
with zipfile.ZipFile(io.BytesIO(blob)) as z:
    assert "molecules/page1_structure1.mol" in z.namelist()
    assert "molecules/page1_structure1.sdf" in z.namelist()
    assert "reviewed_structures.sdf" in z.namelist()
    with io.BytesIO(z.read("reviewed_structures.sdf")) as stream:
        suppl = Chem.ForwardSDMolSupplier(stream)
        molecules = list(suppl)
        assert len(molecules) == 1 and Chem.MolToSmiles(molecules[0]) == "CCO"
print("Ketcher SMILES → MOL → SDF round-trip and review QA passed.")
