"""Lightweight timing and GitHub Actions diagnostics for panChem.

This module intentionally uses only Python's standard library. Timing reports
contain numbers and page indices, never PDF bytes, names, or SMILES.
"""
from datetime import datetime
import io
import json
from pathlib import Path
from time import perf_counter
import zipfile


def _round_seconds(value):
    return round(max(0.0, float(value)), 3)


class PerformanceRecorder:
    """Collect wall-clock timings without affecting chemical inference."""

    def __init__(self):
        self.started = perf_counter()
        self.model_import_seconds = None
        self.pdf_fetch_seconds = None
        self.pages = []
        self.prediction_timings = []
        self.recognition_preload_enabled = False
        self.recognition_preload_seconds = None
        self.recognition_preload_wait_seconds = None

    def now(self):
        return perf_counter()

    def elapsed(self, from_time=None):
        return _round_seconds(self.now() - (self.started if from_time is None else from_time))

    def add_recognition(self, page, index, seconds, cached=False):
        """Record model inference duration without storing structures or SMILES."""
        self.prediction_timings.append({
            "page": int(page),
            "structure": int(index),
            "seconds": _round_seconds(seconds),
            "cache_hit": bool(cached),
        })

    def add_page(self, page, render_seconds, segmentation_seconds,
                 recognition_seconds, structures, valid_smiles, cache_hits=0):
        record = {
            "page": int(page),
            "render_seconds": _round_seconds(render_seconds),
            "segmentation_seconds": _round_seconds(segmentation_seconds),
            "recognition_seconds": _round_seconds(recognition_seconds),
            "structures": int(structures),
            "valid_smiles": int(valid_smiles),
            "cache_hits": int(cache_hits),
        }
        self.pages.append(record)
        print(
            "[PERF] page={page} render={render_seconds:.3f}s "
            "segmentation={segmentation_seconds:.3f}s "
            "recognition={recognition_seconds:.3f}s "
            "structures={structures} valid_smiles={valid_smiles} "
            "cache_hits={cache_hits}".format(**record),
            flush=True,
        )

    def save(self, output_dir):
        """Write a best-effort diagnostic file; never fail extraction for telemetry."""
        report = {
            "schema_version": 1,
            "status": "completed",
            "measurement": "wall_clock_seconds",
            "total_worker_seconds": self.elapsed(),
            "model_import_seconds": self.model_import_seconds,
            "pdf_fetch_seconds": self.pdf_fetch_seconds,
            "recognition_preload_enabled": self.recognition_preload_enabled,
            "recognition_preload_seconds": self.recognition_preload_seconds,
            "recognition_preload_wait_seconds": self.recognition_preload_wait_seconds,
            "pages": self.pages,
            "prediction_timings": self.prediction_timings,
            "recognition_cache_hits": sum(page["cache_hits"] for page in self.pages),
            "total_structures": sum(page["structures"] for page in self.pages),
            "valid_smiles": sum(page["valid_smiles"] for page in self.pages),
        }
        try:
            Path(output_dir, "performance.json").write_text(
                json.dumps(report, indent=2) + "\n", encoding="utf-8"
            )
            print("[PERF] worker_total={:.3f}s".format(report["total_worker_seconds"]), flush=True)
        except OSError as exc:
            print("[PERF] Report unavailable: {}".format(type(exc).__name__), flush=True)
        return report


def performance_from_artifact(blob):
    """Read optional performance.json from GitHub's ZIP artifact."""
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            names = [name for name in archive.namelist()
                     if name.rsplit("/", 1)[-1] == "performance.json"]
            if not names:
                return None
            meta = archive.getinfo(names[0])
            if meta.file_size > 256 * 1024:
                raise ValueError("Diagnostic report is unexpectedly large")
            obj = json.loads(archive.read(names[0]).decode("utf-8"))
            if not isinstance(obj, dict) or obj.get("schema_version") != 1:
                raise ValueError("Unrecognized diagnostic format")
            return obj
    except (zipfile.BadZipFile, KeyError, ValueError, UnicodeError) as exc:
        raise ValueError("Unable to read performance diagnostics") from exc


def _seconds(start, end):
    if not start or not end:
        return None
    try:
        first = datetime.fromisoformat(start.replace("Z", "+00:00"))
        last = datetime.fromisoformat(end.replace("Z", "+00:00"))
        if first.tzinfo is None or last.tzinfo is None:
            return None
        gap = (last - first).total_seconds()
        return _round_seconds(gap) if gap >= 0 else None
    except (AttributeError, TypeError, ValueError):
        return None


def github_timing_report(run, jobs):
    """Summarize official workflow/job/step timestamps, not inferred success."""
    jobs = jobs or []
    job = next((candidate for candidate in jobs
                if candidate.get("name") == "decimer"), {})
    if not job and jobs:
        job = jobs[0]
    steps = [
        {
            "name": step.get("name", ""),
            "seconds": _seconds(step.get("started_at"), step.get("completed_at")),
            "status": step.get("status"),
            "conclusion": step.get("conclusion"),
        }
        for step in job.get("steps", [])
    ]
    status = run.get("status")
    conclusion = run.get("conclusion")
    job_status = job.get("status")
    job_conclusion = job.get("conclusion")
    return {
        "run_id": run.get("id"),
        "workflow_status": status,
        "workflow_conclusion": conclusion,
        "job_status": job_status,
        "job_conclusion": job_conclusion,
        "verified_success": (
            status == "completed" and conclusion == "success"
            and job_status == "completed" and job_conclusion == "success"
        ),
        "queue_seconds": _seconds(run.get("created_at"), job.get("started_at")),
        "job_seconds": _seconds(job.get("started_at"), job.get("completed_at")),
        "steps": steps,
    }
