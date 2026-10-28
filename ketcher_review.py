"""Validate Ketcher drawings before merging them into reviewed SMILES."""
from rdkit import Chem
from rdkit.Chem import rdDepictor


def validated_ketcher_result(payload, molecule_key):
    if not isinstance(payload, dict) or payload.get("action") != "apply":
        raise ValueError("No structure changes to apply")
    if payload.get("molecule_key") != molecule_key:
        raise ValueError("The editor response belongs to a different structure")
    smiles = payload.get("smiles")
    molblock = payload.get("molfile")
    if not isinstance(smiles, str) or not 0 < len(smiles.strip()) <= 10000:
        raise ValueError("The editor did not return valid SMILES text")
    if not isinstance(molblock, str) or not 20 < len(molblock) <= 1000000:
        raise ValueError("The editor did not return a valid MOL file")
    molecule = Chem.MolFromSmiles(smiles.strip())
    if molecule is None or molecule.GetNumAtoms() == 0:
        raise ValueError("The drawing could not be validated as a molecule")
    from_mol = Chem.MolFromMolBlock(molblock, sanitize=True, removeHs=True)
    if from_mol is None or from_mol.GetNumAtoms() == 0:
        raise ValueError("The MOL drawing could not be validated")
    # Compare connectivity ignoring stereochemistry: wedges may be expressed
    # differently by the two export formats; retain isomeric Ketcher SMILES.
    mol_connectivity = Chem.MolToSmiles(from_mol, isomericSmiles=False)
    smiles_connectivity = Chem.MolToSmiles(molecule, isomericSmiles=False)
    if mol_connectivity != smiles_connectivity:
        raise ValueError("SMILES and drawn structure do not represent the same connectivity")
    return Chem.MolToSmiles(molecule, isomericSmiles=True), molblock


def initial_molfile(smiles):
    """Use RDKit to generate 2D input for Ketcher without requiring SMILES import."""
    molecule = Chem.MolFromSmiles(smiles or "") if smiles else None
    if molecule is None or not molecule.GetNumAtoms():
        return ""
    copy = Chem.Mol(molecule)
    rdDepictor.Compute2DCoords(copy)
    return Chem.MolToMolBlock(copy)


def result_molfile(smiles, optional_molfile=""):
    """Export exactly the reviewed molecule; avoid returning stale editor geometry."""
    molecule = Chem.MolFromSmiles(smiles or "") if smiles else None
    if molecule is None:
        return None
    if optional_molfile:
        trial = Chem.MolFromMolBlock(optional_molfile, sanitize=True, removeHs=True)
        if trial is not None:
            if Chem.MolToSmiles(trial, isomericSmiles=False) == Chem.MolToSmiles(molecule, isomericSmiles=False):
                return optional_molfile
    copy = Chem.Mol(molecule)
    rdDepictor.Compute2DCoords(copy)
    return Chem.MolToMolBlock(copy)
