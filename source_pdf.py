"""Restore the PDF belonging to a DECIMER result without asking for a second upload.

Source priority: original source.pdf attached to results ZIP, then the
corresponding GitHub Actions run's commit/job ID. A different run's PDF
must never be reused.
"""
from __future__ import annotations

import base64
import io
import re
import zipfile

MAX_PDF = 25 * 1024 * 1024
JOB_PATTERN = re.compile(r"^panChem PDF job ([0-9a-f]{12}) pages=[0-9,\-]+(?:\s|$)")


def extract_source_pdf(artifact):
    if not artifact:
        return None
    with zipfile.ZipFile(io.BytesIO(artifact)) as archive:
        found = [n for n in archive.namelist()
                 if n == "source.pdf" or n.endswith("/source.pdf")]
        if not found:
            return None
        info = archive.getinfo(found[0])
        if info.file_size > MAX_PDF:
            raise ValueError("Source PDF exceeds 25 MB")
        data = archive.read(found[0])
        return validated_pdf(data)


def validated_pdf(data):
    if not data or not data.startswith(b"%PDF") or len(data) > MAX_PDF:
        raise ValueError("Source PDF is missing, malformed or oversized")
    return data


def resolve_pdf_for_run(run_id, token, api_request, repository_api, artifact=None):
    """Return (PDF bytes, job ID or None, explanatory status).

    Never take a PDF from another run/session; GitHub job IDs are derived from
    the exact workflow run's head commit message.
    """
    pdf = extract_source_pdf(artifact)
    if pdf:
        return pdf, None, "artifact"
    if not run_id or not token:
        return None, None, "No matching source PDF or GitHub run credentials"
    run = api_request("GET", f"{repository_api}/actions/runs/{int(run_id)}", token).json()
    message = ((run.get("head_commit") or {}).get("message") or "").strip()
    matched = JOB_PATTERN.match(message)
    if not matched:
        return None, None, "The run does not identify a panChem PDF upload job"
    job_id = matched.group(1)
    record = api_request("GET", f"{repository_api}/contents/jobs/{job_id}/paper.pdf",
                         token).json()
    if record.get("encoding") != "base64" or not record.get("content"):
        return None, job_id, "Source PDF content is not available in GitHub"
    pdf = base64.b64decode(record["content"])
    return validated_pdf(pdf), job_id, "github"
