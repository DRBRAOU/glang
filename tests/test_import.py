"""Bulk CSV + ZIP import."""
from __future__ import annotations

import io
import zipfile

from tests.conftest import make_wav

HEADER = "expression_id,category,english,telugu,gondi_text,variety,script,audio_filename\n"


def build(n=100, missing=(), dupes=(), extra_rows=""):
    rows = [HEADER]
    for i in range(1, n + 1):
        code = f"GON-{i:03d}"
        rows.append(f"{code},Greetings,Phrase {i},పదం {i},,Adilabad,,{code}.wav\n")
    for d in dupes:
        rows.append(f"{d},Greetings,Dup,నకలు,,Adilabad,,dup.wav\n")
    rows.append(extra_rows)
    csv_bytes = "".join(rows).encode("utf-8")
    zbuf = io.BytesIO()
    wav = make_wav(seconds=0.5)
    with zipfile.ZipFile(zbuf, "w", zipfile.ZIP_DEFLATED) as z:
        for i in range(1, n + 1):
            if i in missing:
                continue
            z.writestr(f"audio/GON-{i:03d}.wav", wav)
        z.writestr("dup.wav", wav)
    return csv_bytes, zbuf.getvalue()


def preview(staff, csv_bytes, zip_bytes=None):
    files = {"csv_file": ("data.csv", csv_bytes, "text/csv")}
    if zip_bytes is not None:
        files["zip_file"] = ("audio.zip", zip_bytes, "application/zip")
    return staff.post("/api/admin/imports/preview", files=files)


def test_100_rows_preview_and_commit(contributor, public):
    csv_bytes, zip_bytes = build(100)
    r = preview(contributor, csv_bytes, zip_bytes)
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["total"] == 100 and job["accepted"] == 100 and job["rejected"] == 0
    assert job["status"] == "previewed"
    # Dry run must not create anything
    assert contributor.get("/api/admin/expressions").json() == []
    # Blank Gondi text produces warnings, not errors
    assert any("gondi_text is blank" in w for w in job["report"]["rows"][0]["warnings"])

    r = contributor.post(f"/api/admin/imports/{job['id']}/commit", json={})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "committed"
    exprs = contributor.get("/api/admin/expressions").json()
    assert len(exprs) == 100
    assert {e["status"] for e in exprs} == {"draft"}
    assert all(e["has_default_audio"] for e in exprs)
    # nothing is public
    assert public.get("/api/public/expressions").json()["total"] == 0
    # committing twice is refused
    assert contributor.post(f"/api/admin/imports/{job['id']}/commit", json={}).status_code == 409
    # importing the same CSV again flags duplicates against the library
    again = preview(contributor, csv_bytes, zip_bytes).json()
    assert again["rejected"] == 100
    assert "already exists" in again["report"]["rows"][0]["errors"][0]


def test_row_level_errors(contributor):
    extra = (
        "GON-500,,No category,,,Adilabad,,GON-500.wav\n"   # missing category + missing file
        "bad id!,Greetings,x,y,,,,x.wav\n"                    # invalid id
        "GON-501,Greetings,,,,,,../evil.wav\n"                # path in filename, no meaning
        "GON-502,Greetings,Ok,సరే,,,,notes.txt\n"             # unsupported extension
    )
    csv_bytes, zip_bytes = build(5, missing={2}, dupes=["GON-001"], extra_rows=extra)
    job = preview(contributor, csv_bytes, zip_bytes).json()
    rows = {r["line"]: r for r in job["report"]["rows"]}
    errors = " | ".join(e for r in rows.values() for e in r["errors"])
    assert "was not found in the ZIP" in errors          # GON-002 missing
    assert "Duplicate expression_id" in errors           # GON-001 twice
    assert "category is required" in errors
    assert "may contain only letters" in errors
    assert "plain file name" in errors
    assert "At least one meaning" in errors
    assert "Unsupported audio file type" in errors
    assert job["accepted"] == 4  # GON-001 (first occurrence), 003, 004, 005
    # Commit is refused while errors exist...
    r = contributor.post(f"/api/admin/imports/{job['id']}/commit", json={})
    assert r.status_code == 409
    # ...unless the user explicitly chooses to import only valid rows
    r = contributor.post(f"/api/admin/imports/{job['id']}/commit", json={"skip_invalid": True})
    assert r.status_code == 200 and r.json()["accepted"] == 4


def test_bad_utf8_and_missing_columns(contributor):
    r = preview(contributor, "expression_id,category\nGON-1,Greetings\n".encode())
    assert r.status_code == 422 and "missing required column" in r.json()["detail"]
    latin1 = (HEADER + "GON-001,Greetings,Café,,,,,a.wav\n").encode("latin-1")
    r = preview(contributor, latin1)
    assert r.status_code == 422 and "UTF-8" in r.json()["detail"]
    r = preview(contributor, HEADER.encode())
    assert r.status_code == 422


def test_unsafe_zip_rejected(contributor):
    csv_bytes, _ = build(1)
    for evil in ("../../etc/passwd.wav", "/abs/GON-001.wav", "C:/x/GON-001.wav"):
        zbuf = io.BytesIO()
        with zipfile.ZipFile(zbuf, "w") as z:
            z.writestr(evil, make_wav(0.3))
        r = preview(contributor, csv_bytes, zbuf.getvalue())
        assert r.status_code == 422 and "Unsafe ZIP entry" in r.json()["detail"], evil
    # symlink entry
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w") as z:
        info = zipfile.ZipInfo("GON-001.wav")
        info.external_attr = (0o120777 << 16)
        z.writestr(info, "/etc/passwd")
    r = preview(contributor, csv_bytes, zbuf.getvalue())
    assert r.status_code == 422 and "symbolic link" in r.json()["detail"]
    # not a zip at all
    r = preview(contributor, csv_bytes, b"PK\x03\x04garbage")
    assert r.status_code == 422


def test_fake_audio_in_zip_reported(contributor):
    csv_bytes = (HEADER + "GON-001,Greetings,Hello,నమస్కారం,,Adilabad,,GON-001.mp3\n").encode()
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w") as z:
        z.writestr("GON-001.mp3", b"#!/bin/sh\nrm -rf /\n" * 10)
    job = preview(contributor, csv_bytes, zbuf.getvalue()).json()
    assert job["rejected"] == 1
    assert "Audio problem" in job["report"]["rows"][0]["errors"][0]


def test_formula_content_stored_as_text(contributor):
    csv_bytes = (HEADER + 'GON-001,Greetings,"=HYPERLINK(""http://x"")",నమస్కారం,,Adilabad,,GON-001.wav\n').encode()
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w") as z:
        z.writestr("GON-001.wav", make_wav(0.3))
    job = preview(contributor, csv_bytes, zbuf.getvalue()).json()
    assert job["accepted"] == 1
    assert any("formula" in w for w in job["report"]["rows"][0]["warnings"])
    contributor.post(f"/api/admin/imports/{job['id']}/commit", json={})
    e = contributor.get("/api/admin/expressions").json()[0]
    assert e["english"].startswith("=HYPERLINK")
    # Export escapes it so spreadsheet apps will not execute it
    export = contributor.get("/api/admin/expressions/export.csv").content.decode("utf-8-sig")
    assert "'=HYPERLINK" in export


def test_template_download(contributor):
    r = contributor.get("/api/admin/imports/template.csv")
    assert r.status_code == 200
    text = r.content.decode("utf-8-sig")
    assert text.startswith("expression_id,category,english,telugu,gondi_text,variety,script,audio_filename")


# ----------------------------------------------------------------------------- prompts file & update mode
from pathlib import Path  # noqa: E402

PROMPTS = Path(__file__).resolve().parent.parent / "samples" / "gondi-vaani-100-prompts.csv"


def _commit(staff, job, **kw):
    r = staff.post(f"/api/admin/imports/{job['id']}/commit", json=kw)
    assert r.status_code == 200, r.text
    return r.json()


def test_prompts_file_imports_as_text_only(admin, public):
    job = preview(admin, PROMPTS.read_bytes()).json()
    assert job["status"] == "previewed", job
    assert job["total"] == 100 and job["accepted"] == 100 and job["rejected"] == 0
    assert any("text only" in w for w in job["report"]["warnings"])
    _commit(admin, job)
    exprs = admin.get("/api/admin/expressions").json()
    assert len(exprs) == 100
    assert {e["status"] for e in exprs} == {"draft"}
    assert not any(e["recordings"] for e in exprs)
    first = admin.get(f"/api/admin/expressions/{exprs[0]['id']}").json()
    assert first["telugu"] == "నమస్కారం" and first["english"] == "Hello" and first["gondi_text"] == ""
    assert "Pending expert validation" in first["provenance"]
    cats = [c["name"] for c in admin.get("/api/admin/categories").json()]
    assert cats == ["Greetings", "Introductions", "Family", "Numbers", "Everyday Words",
                    "Everyday Conversations", "Useful Sentences"]
    assert public.get("/api/public/expressions").json()["total"] == 0


def test_update_mode_replaces_meanings_but_keeps_expert_gondi(admin):
    _commit(admin, preview(admin, PROMPTS.read_bytes()).json())
    e1 = next(e for e in admin.get("/api/admin/expressions").json() if e["expression_code"] == "GON-001")
    admin.patch(f"/api/admin/expressions/{e1['id']}", json={"gondi_text": "[expert text placeholder]"})

    revised = PROMPTS.read_text(encoding="utf-8-sig").replace("GON-001,Greetings,Hello,", "GON-001,Greetings,Hello (greeting),")
    revised += "GON-101,Useful Sentences,New prompt,కొత్త వాక్యం,,,,GON-101.mp3,,,Pending expert validation,\n"
    # create mode refuses existing IDs and points to update mode
    job = preview(admin, revised.encode()).json()
    assert job["rejected"] == 100 and "update existing" in job["report"]["rows"][0]["errors"][0]
    # update mode
    r = admin.post("/api/admin/imports/preview", files={"csv_file": ("v2.csv", revised.encode(), "text/csv")},
                   data={"mode": "update"})
    job = r.json()
    assert job["rejected"] == 0 and job["report"]["creates"] == 1 and job["report"]["updates"] == 100
    row1 = job["report"]["rows"][0]
    assert row1["action"] == "update" and row1["changes"] == ["english"]
    assert "No changes" in job["report"]["rows"][1]["warnings"][0]
    _commit(admin, job)
    d = admin.get(f"/api/admin/expressions/{e1['id']}").json()
    assert d["english"] == "Hello (greeting)"
    assert d["gondi_text"] == "[expert text placeholder]"  # blank cell did not erase it
    assert d["reviews"][0]["decision"] == "updated"
    assert len(admin.get("/api/admin/expressions").json()) == 101


def test_update_mode_adds_audio_and_respects_roles(admin, contributor, consenting_speaker):
    from tests.test_workflow import PLACEHOLDER

    csv1 = (HEADER + "GON-001,Greetings,Hello,నమస్కారం,,Adilabad,,GON-001.wav\n"
                     "GON-002,Greetings,Thanks,ధన్యవాదాలు,,Adilabad,,GON-002.wav\n").encode()
    _commit(contributor, preview(contributor, csv1).json())
    exprs = {e["expression_code"]: e for e in admin.get("/api/admin/expressions").json()}
    # move GON-002 to review so a contributor may no longer change it
    e2 = exprs["GON-002"]["id"]
    admin.patch(f"/api/admin/expressions/{e2}", json={"gondi_text": PLACEHOLDER})
    admin.post(f"/api/admin/expressions/{e2}/recordings", files={"file": ("a.wav", make_wav(), "audio/wav")},
               data={"speaker_id": str(consenting_speaker)})
    assert admin.post(f"/api/admin/expressions/{e2}/transition", json={"action": "submit"}).status_code == 200

    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w") as z:
        z.writestr("GON-001.wav", make_wav(0.5))
        z.writestr("GON-002.wav", make_wav(0.5))
    files = {"csv_file": ("d.csv", csv1, "text/csv"), "zip_file": ("a.zip", zbuf.getvalue(), "application/zip")}
    job = contributor.post("/api/admin/imports/preview", files=files, data={"mode": "update"}).json()
    errs = {r["expression_id"]: r["errors"] for r in job["report"]["rows"]}
    assert errs["GON-001"] == [] and "only an administrator" in errs["GON-002"][0]
    _commit(contributor, job, skip_invalid=True)
    d1 = admin.get(f"/api/admin/expressions/{exprs['GON-001']['id']}").json()
    assert len(d1["recordings"]) == 1 and d1["recordings"][0]["is_default"]

    # an admin update of the in-review entry adds a recording and keeps its status (no text changed)
    job = admin.post("/api/admin/imports/preview", files={**files, "zip_file": ("a.zip", zbuf.getvalue(), "application/zip")},
                     data={"mode": "update"}).json()
    _commit(admin, job, skip_invalid=True)
    d2 = admin.get(f"/api/admin/expressions/{e2}").json()
    assert d2["status"] == "in_review" and len(d2["recordings"]) == 2


def test_cli_import_csv(admin, capsys):
    from app.cli import main

    assert main(["import-csv", str(PROMPTS), "--as", "admin1"]) == 0          # dry run only
    assert admin.get("/api/admin/expressions").json() == []
    assert main(["import-csv", str(PROMPTS), "--as", "admin1", "--commit"]) == 0
    assert len(admin.get("/api/admin/expressions").json()) == 100
    out = capsys.readouterr().out
    assert "100 valid" in out and "Imported 100 rows" in out
