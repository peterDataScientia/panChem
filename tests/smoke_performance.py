"""Smoke tests for per-page DECIMER timing and GitHub diagnostics.

Runs without loading TensorFlow or real DECIMER model weights.
"""
import io
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile
from types import ModuleType
from unittest.mock import patch
import zipfile

import numpy as np
from PIL import Image
import worker
from performance_diagnostics import (
    PerformanceRecorder, github_timing_report, performance_from_artifact,
)

rec = PerformanceRecorder()
rec.model_import_seconds = 0.125
rec.add_page(2, 0.3, 1.6, 0.8, 2, 1)
with tempfile.TemporaryDirectory() as tmp:
    report = rec.save(tmp)
    assert report["status"] == "completed"
    assert report["total_structures"] == 2
    assert report["valid_smiles"] == 1
    assert report["pages"][0]["segmentation_seconds"] == 1.6
    data = Path(tmp, "performance.json").read_bytes()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as output:
        output.writestr("results/performance.json", data)
    artifact = buffer.getvalue()
    recovered = performance_from_artifact(artifact)
    assert recovered["model_import_seconds"] == 0.125

def archive_with(filename, value):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as output:
        output.writestr(filename, value)
    return buf.getvalue()

assert performance_from_artifact(archive_with("results/results.csv", "a,b\n")) is None
assert performance_from_artifact(archive_with("results/performance.json", json.dumps({
    "schema_version": 1, "pages": [], "total_worker_seconds": 7.5,
})))["total_worker_seconds"] == 7.5

run = {"id": 111, "status": "completed", "conclusion": "success",
       "created_at": "2026-10-28T00:00:00Z"}
jobs = [{"name": "decimer", "status": "completed", "conclusion": "success",
         "started_at": "2026-10-28T00:00:10Z",
         "completed_at": "2026-10-28T00:02:00Z",
         "steps": [
             {"name": "Initialize containers", "status": "completed",
              "conclusion": "success",
              "started_at": "2026-10-28T00:00:12Z",
              "completed_at": "2026-10-28T00:01:00Z"},
         ]}]
timing = github_timing_report(run, jobs)
assert timing["queue_seconds"] == 10.0
assert timing["job_seconds"] == 110.0
assert timing["steps"][0]["seconds"] == 48.0
assert timing["verified_success"]
assert not github_timing_report({**run, "conclusion": "failure"}, jobs)["verified_success"]
assert not github_timing_report(run, [{**jobs[0], "conclusion": None}])["verified_success"]


class DummyDoc:
    def __init__(self, path):
        self.path = path
    def __len__(self):
        return 1
    def __getitem__(self, number):
        assert number == 0
        return self
    def render(self, scale):
        assert scale == 300 / 72
        return self
    def to_pil(self):
        return Image.new("RGB", (16, 16), "white")
    def close(self):
        pass


fake_segmentation = ModuleType("decimer_segmentation")
fake_segmentation.segment_chemical_structures_from_file = (
    lambda filename, expand, return_bboxes:
        ([np.zeros((8, 8, 3), dtype=np.uint8)] * 2,
         [(0, 0, 8, 8), (8, 8, 16, 16)])
)
fake_recognition = ModuleType("DECIMER")
recognition_calls = []
preload_calls = []

def fake_predict(image):
    assert isinstance(image, np.ndarray), "Must pass pixels directly, not a PNG filename"
    assert image.dtype == np.uint8 and image.shape == (8, 8, 3)
    recognition_calls.append(image.copy())
    return "CCO"

def fake_preload(printed=True, hand_drawn=False):
    assert printed and not hand_drawn
    preload_calls.append(True)

fake_recognition.predict_SMILES = fake_predict
fake_recognition.load_models = fake_preload

with tempfile.TemporaryDirectory() as tmp:
    output = Path(tmp, "remote")
    with patch.dict(sys.modules, {
        "decimer_segmentation": fake_segmentation, "DECIMER": fake_recognition,
    }), patch.object(worker.pdfium, "PdfDocument", DummyDoc), patch.object(
        worker, "fetch_pdf", side_effect=lambda url, destination:
            destination.write_bytes(b"%PDF-1.7\n")
    ), patch.object(worker, "publish", return_value=True), patch.dict(
        os.environ, {"PANCHEM_PARALLEL_MODEL_PRELOAD": "1"}
    ):
        worker.run("https://example.org/test.pdf", "1", output)
    result = json.loads(Path(output, "performance.json").read_text())
    assert result["total_structures"] == 2
    assert result["valid_smiles"] == 2
    assert result["recognition_preload_enabled"]
    assert result["recognition_cache_hits"] == 1
    assert len(preload_calls) == 1
    assert len(recognition_calls) == 1, "Duplicate image should not be inferred twice"
    assert len(result["pages"]) == 1
    assert result["prediction_timings"][0]["cache_hit"] is False
    assert result["prediction_timings"][1]["cache_hit"] is True
    assert Path(output, "results.csv").is_file()
    assert Path(output, "results.smi").read_text().startswith("CCO ")
    with Image.open(output / "page_1_structure_1.png") as saved:
        np.testing.assert_array_equal(recognition_calls[0], np.asarray(saved.convert("RGB")))
    with (output / "results.csv").open(encoding="utf-8") as stream:
        result_lines = stream.read().splitlines()
    assert len(result_lines) == 3, "Both identical depictions retain separate CSV rows"

    # Exercise the other, local-file entrypoint as well.
    cwd = Path.cwd()
    worker_script = cwd / "worker.py"
    os.chdir(tmp)
    try:
        local_pdf = Path("jobs/abcdef012345/paper.pdf")
        local_pdf.parent.mkdir(parents=True)
        local_pdf.write_bytes(b"%PDF-1.7\n")
        local_output = Path("local")
        with patch.dict(sys.modules, {
            "decimer_segmentation": fake_segmentation, "DECIMER": fake_recognition,
        }), patch.object(worker.pdfium, "PdfDocument", DummyDoc), patch.object(
            sys, "argv", ["worker.py", "--file", str(local_pdf),
                          "--pages", "1", "--output", str(local_output)]
        ), patch.dict(os.environ, {"PANCHEM_PARALLEL_MODEL_PRELOAD": "0"}):
            runpy.run_path(str(worker_script), run_name="__main__")
        local_result = json.loads(Path(local_output, "performance.json").read_text())
        assert local_result["total_structures"] == 2
        assert local_result["recognition_cache_hits"] == 1
        assert not local_result["recognition_preload_enabled"]
        assert len(recognition_calls) == 2, "Cache must reset for each PDF job"
        assert len(preload_calls) == 1, "Opt-out should not start background loading"
        assert Path(local_output, "results.csv").is_file()
        assert Path(local_output, "page_1_structure_1.png").is_file()
        assert Path(local_output, "page_1_structure_2.png").is_file()
    finally:
        os.chdir(cwd)

assert "results/performance.json" in Path(".github/workflows/decimer.yml").read_text()
assert "performance_diagnostics_panel()" in Path("app.py").read_text()
print("Optimized DECIMER inference, pixel caching, preload switch, timings and artifacts: PASSED")
