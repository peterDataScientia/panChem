"""Smoke checks for chemical SVG rendering on Linux/Streamlit deployments."""
import sys
from unittest.mock import patch

from rdkit import Chem
from chemqa import assess, molecule_svg

examples = {
    "phenol": "Oc1ccccc1",
    "acetate": "CC(=O)[O-]",
    "aspirin": "CC(=O)Oc1ccccc1C(=O)O",
    "stereochemistry": "N[C@@H](C)C(=O)O",
}
for label, smiles in examples.items():
    qa, canonical, inchikey, molecule = assess(smiles)
    assert qa == "VALID_SYNTAX", (label, qa)
    assert molecule is not None
    svg = molecule_svg(molecule)
    assert svg and svg.lstrip().startswith("<svg"), (label, "native SVG missing")
    assert "</svg>" in svg
    with patch.dict(sys.modules, {"rdkit.Chem.Draw": None}):
        fallback = molecule_svg(molecule)
    assert fallback and fallback.lstrip().startswith("<svg"), (label, "fallback SVG missing")
    assert "</svg>" in fallback
    print(f"{label}: native SVG + fallback SVG passed")

status, *_ = assess("[R]CC")
assert status == "MARKUSH_REVIEW"
print("All 2D drawing checks passed.")
