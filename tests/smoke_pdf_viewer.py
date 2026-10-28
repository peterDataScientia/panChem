"""Smoke-test the fullscreen PDF preview HTML and JavaScript."""
import base64
import json
import re
import shutil
import subprocess

from pathlib import Path
from pdf_viewer import full_page_viewer


sample = b"%PDF-1.4\n%panChem viewer smoke test\n%%EOF\n"
html = full_page_viewer(sample, initial_page=3)

assert "__PDF_BASE64__" not in html
assert "__INITIAL_PAGE__" not in html
assert 'const startPage = 3;' in html
assert "requestFullscreen" in html
assert "fullscreenchange" in html
assert "Slide PDF pages" in html
assert 'mode = "page", zoom = 1.0' in html
assert 'id="range" type="range"' in html
assert 'id="full"' in html
assert 'id="pageInput"' in html
assert "Fit width" in html and "Fit page" in html
assert "touchend" in html and "ArrowRight" in html
assert "3.11.174/pdf.min.js" in html
assert "3.11.174/pdf.worker.min.js" in html

match = re.search(r'const encoded = ("[^"]+");', html)
assert match, "PDF data missing"
assert base64.b64decode(json.loads(match.group(1))) == sample
assert 'const startPage = 1;' in full_page_viewer(sample, initial_page=0)

scripts = re.findall(r'<script>(.*?)</script>', html, flags=re.DOTALL)
assert len(scripts) == 1, "Unexpected inline JS blocks"
assert shutil.which("node"), "Node.js required for JavaScript syntax check"
result = subprocess.run(
    ["node", "--check", "-"], input=scripts[0], text=True,
    capture_output=True, check=False,
)
assert result.returncode == 0, result.stderr
app = Path("app.py").read_text(encoding="utf-8")
assert 'components.html(\n        full_page_viewer(data)' in app
assert 'st.slider(' not in app, "PDF slider must be inside the interactive preview"
assert 'st.toggle(' not in app, "Preview should be interactive by default"
print("Default interactive full-page PDF preview, integrated slider and JS syntax: PASSED")
