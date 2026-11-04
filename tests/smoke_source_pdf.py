"""Source-PDF linkage: ZIP retrieval, GitHub run fallback and mismatch safety."""
import base64
import io
import zipfile
from source_pdf import extract_source_pdf, resolve_pdf_for_run

original=b"%PDF-1.4\nsample test PDF\n"
archive=io.BytesIO()
with zipfile.ZipFile(archive,"w") as zipout:
    zipout.writestr("results/results.csv","page,smiles\n")
    zipout.writestr("results/source.pdf",original)
assert extract_source_pdf(archive.getvalue())==original

def forbidden_api(*args,**kwargs):
    raise AssertionError("Must not call GitHub when attached PDF is available")
pdf,job,origin=resolve_pdf_for_run(123,"token",forbidden_api,"https://api.github.com/repos/example/repo",archive.getvalue())
assert pdf==original and origin=="artifact"

seen=[]
class Reply:
    def __init__(self,data): self.data=data
    def json(self): return self.data
def request(method,url,token):
    seen.append(url)
    if "/actions/runs/" in url:
        return Reply({"head_commit":{"message":"panChem PDF job abc123def456 pages=4"}})
    if url.endswith("jobs/abc123def456/paper.pdf"):
        return Reply({"encoding":"base64","content":base64.b64encode(original).decode()})
    raise AssertionError("Unexpected request: "+url)
pdf,job,origin=resolve_pdf_for_run(456,"token",request,"https://api.github.com/repos/example/repo")
assert pdf==original and job=="abc123def456" and origin=="github"
assert len(seen)==2

# GitHub Contents API returns empty content for files > 1 MB.
def big_pdf_request(method,url,token):
    if "/actions/runs/" in url:
        return Reply({"head_commit":{"message":"panChem PDF job abc123def456 pages=4"}})
    if url.endswith("/contents/jobs/abc123def456/paper.pdf"):
        return Reply({"encoding":"none","size":1647440,"sha":"a"*40,"content":""})
    if url.endswith("/git/blobs/"+"a"*40):
        return Reply({"encoding":"base64","content":base64.b64encode(original).decode()})
    raise AssertionError("Unexpected URL " + url)
large, job, origin = resolve_pdf_for_run(
    41628860887, "token", big_pdf_request, "https://api.github.com/repos/example/repo"
)
assert large == original and origin == "github-blob" and job == "abc123def456"

def wrong_run(method,url,token):
    return Reply({"head_commit":{"message":"Some unrelated commit"}})
pdf,job,info=resolve_pdf_for_run(345,"token",wrong_run,"https://api.github.com/repos/example/repo")
assert pdf is None and job is None
print("Source-PDF artifact and matching-run restoration: PASS")
