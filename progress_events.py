"""Best-effort push-only DECIMER progress events.

Never publish PDF contents, filenames, predicted SMILES or chemical structures.
The public ntfy topic only carries anonymized job stage/counts.
"""
import json
import os
import re
import sys

import requests

RELAY_BASE = "https://ntfy.sh"
TOPIC_RE = re.compile(r"^panchem-[0-9a-f]{12}$")


def topic_for_job(job_id):
    if not re.fullmatch(r"[0-9a-f]{12}", job_id or ""):
        return ""
    return f"panchem-{job_id}"


def publish(stage, **payload):
    """Send a small progress event immediately; failures cannot abort extraction."""
    topic = os.environ.get("PANCHEM_PROGRESS_TOPIC", "")
    if not TOPIC_RE.fullmatch(topic):
        return False
    approved = {"page", "total_pages", "structures", "page_structures", "completed_pages", "stage"}
    event = {"stage": str(stage)[:40]}
    if os.environ.get("GITHUB_RUN_ID", "").isdigit():
        event["run_id"] = int(os.environ["GITHUB_RUN_ID"])
    for name, value in payload.items():
        if name in approved and isinstance(value, (int, str)) and not isinstance(value, bool):
            event[name] = value
    try:
        response = requests.post(
            f"{RELAY_BASE}/{topic}",
            data=json.dumps(event, separators=(",", ":")).encode("utf-8"),
            headers={"Content-Type": "text/plain; charset=utf-8", "Firebase": "no"},
            timeout=7,
        )
        response.raise_for_status()
        return True
    except requests.RequestException as error:
        print(f"[panChem progress] Relay unavailable: {error}", file=sys.stderr, flush=True)
        return False


def publish_from_github_event(stage):
    """Extract job identity from the GitHub event and publish its final job stage."""
    message = ""
    path = os.environ.get("GITHUB_EVENT_PATH", "")
    try:
        with open(path, encoding="utf-8") as source:
            event = json.load(source)
        message = (event.get("head_commit") or {}).get("message", "").strip()
    except (OSError, ValueError, TypeError):
        return False
    match = re.fullmatch(r"panChem PDF job ([0-9a-f]{12}) pages=[0-9,-]+", message)
    if not match:
        return False
    os.environ["PANCHEM_PROGRESS_TOPIC"] = topic_for_job(match.group(1))
    return publish(stage)


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] in ("starting", "results_ready", "failed"):
        if not publish_from_github_event(sys.argv[1]):
            print("Progress notification was not delivered (extraction is unaffected).")
    else:
        raise SystemExit("Usage: progress_events.py starting|results_ready|failed")
