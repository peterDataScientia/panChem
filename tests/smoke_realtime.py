"""Check SSE bridge syntax, topic validation and failure-safe event publishing."""
import os
import re
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import progress_events

html = Path("components/live_progress/index.html").read_text(encoding="utf-8")
assert "new EventSource(" in html
assert "streamlit:setComponentValue" in html
assert "streamlit:componentReady" in html
assert "run_every" not in html
assert "run_id:Number(data.run_id||0)" in html
assert shutil.which("node")
script = re.search(r"<script>(.*?)</script>", html, re.S)
assert script
result = subprocess.run(
    ["node", "--check", "-"], input=script.group(1),
    text=True, capture_output=True, check=False,
)
assert result.returncode == 0, result.stderr

assert progress_events.topic_for_job("abcdef012345") == "panchem-abcdef012345"
assert progress_events.topic_for_job("not-valid") == ""
assert progress_events.topic_for_job("../../foo") == ""

class MockResponse:
    def raise_for_status(self):
        return None

with patch.dict(os.environ, {
    "PANCHEM_PROGRESS_TOPIC": "panchem-abcdef012345",
    "GITHUB_RUN_ID": "40599912345",
}):
    with patch.object(progress_events.requests, "post", return_value=MockResponse()) as sent:
        assert progress_events.publish("page_complete", page=2, total_pages=6,
                                       structures=11, private_smiles="CCO")
        args, kwargs = sent.call_args
        assert args[0] == "https://ntfy.sh/panchem-abcdef012345"
        body = kwargs["data"].decode("utf-8")
        assert '"run_id":40599912345' in body
        assert '"structures":11' in body
        assert "private_smiles" not in body
with patch.dict(os.environ, {"PANCHEM_PROGRESS_TOPIC": "invalid"}):
    assert not progress_events.publish("starting")
print("Event-driven SSE, secure schema and JS protocol smoke tests: PASSED")
