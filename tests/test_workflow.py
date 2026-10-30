"""Learner visibility, editorial workflow, roles and audio access control."""
from __future__ import annotations

from tests.conftest import ffmpeg_encode, make_wav

# Placeholder text only: real Gondi text must come from experts, never from code or tests.
PLACEHOLDER = "[test placeholder - not Gondi]"


def create_expr(staff, code="GON-001", **extra):
    body = {"expression_code": code, "english": "Hello", "telugu": "నమస్కారం", "variety": "Adilabad", **extra}
    r = staff.post("/api/admin/expressions", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def upload(staff, expr_id, data=None, name="clip.wav", speaker_id=None, make_default=True):
    form = {"make_default": "true" if make_default else "false"}
    if speaker_id:
        form["speaker_id"] = str(speaker_id)
    return staff.post(f"/api/admin/expressions/{expr_id}/recordings",
                      files={"file": (name, data or make_wav(), "application/octet-stream")}, data=form)


def publish_flow(admin, contributor, reviewer, speaker_id, code="GON-001"):
    e = create_expr(contributor, code, gondi_text=PLACEHOLDER)
    r = upload(contributor, e["id"], speaker_id=speaker_id)
    assert r.status_code == 201, r.text
    assert contributor.post(f"/api/admin/expressions/{e['id']}/transition", json={"action": "submit"}).status_code == 200
    r = reviewer.post(f"/api/admin/expressions/{e['id']}/transition", json={"action": "approve", "comments": "Checked."})
    assert r.status_code == 200, r.text
    r = admin.post(f"/api/admin/expressions/{e['id']}/transition", json={"action": "publish"})
    assert r.status_code == 200, r.text
    return r.json()


# ----------------------------------------------------------------------------- visibility
def test_learner_cannot_see_drafts_or_unapproved_audio(public, contributor):
    e = create_expr(contributor, gondi_text=PLACEHOLDER)
    r = upload(contributor, e["id"])
    assert r.status_code == 201
    rec_id = r.json()["recordings"][0]["id"]

    assert public.get("/api/public/expressions").json() == {"items": [], "total": 0}
    assert public.get(f"/api/public/expressions/{e['id']}").status_code == 404
    assert public.get(f"/api/public/audio/{rec_id}").status_code == 404
    # The staff preview endpoint is not reachable without login either.
    assert public.get(f"/api/admin/recordings/{rec_id}/audio").status_code == 401
    assert public.get("/api/admin/expressions").status_code == 401


def test_full_publish_flow_and_streaming(public, admin, contributor, reviewer, consenting_speaker):
    detail = publish_flow(admin, contributor, reviewer, consenting_speaker)
    assert detail["status"] == "published"

    items = public.get("/api/public/expressions").json()["items"]
    assert len(items) == 1
    item = items[0]
    assert item["gondi_text"] == PLACEHOLDER and item["speaker"] == "Voice: Test Speaker"

    audio = public.get(item["audio_url"])
    assert audio.status_code == 200
    assert audio.headers["content-type"].startswith("audio/")
    assert audio.content[:4] == b"RIFF"
    # Range requests are needed by iOS Safari and for seeking.
    part = public.get(item["audio_url"], headers={"Range": "bytes=0-99"})
    assert part.status_code == 206 and len(part.content) == 100

    cats = public.get("/api/public/categories").json()
    assert cats == []  # no category assigned in this test

    # Search in Telugu and English
    assert public.get("/api/public/expressions", params={"q": "నమస్"}).json()["total"] == 1
    assert public.get("/api/public/expressions", params={"q": "hello"}).json()["total"] == 1
    assert public.get("/api/public/expressions", params={"q": "zzz"}).json()["total"] == 0


def test_second_unapproved_recording_not_public(public, admin, contributor, reviewer, consenting_speaker):
    detail = publish_flow(admin, contributor, reviewer, consenting_speaker)
    r = upload(admin, detail["id"], speaker_id=consenting_speaker)
    assert r.status_code == 201
    recs = r.json()["recordings"]
    new = [x for x in recs if not x["is_default"]][0]
    assert new["status"] == "pending"
    assert public.get(f"/api/public/audio/{new['id']}").status_code == 404
    # cannot make a pending recording the default on a published entry
    r = admin.patch(f"/api/admin/recordings/{new['id']}", json={"is_default": True})
    assert r.status_code == 409


def test_unpublish_hides_audio_immediately(public, admin, contributor, reviewer, consenting_speaker):
    d = publish_flow(admin, contributor, reviewer, consenting_speaker)
    url = public.get("/api/public/expressions").json()["items"][0]["audio_url"]
    assert admin.post(f"/api/admin/expressions/{d['id']}/transition", json={"action": "unpublish"}).status_code == 200
    assert public.get(url).status_code == 404
    assert public.get("/api/public/expressions").json()["total"] == 0


# ----------------------------------------------------------------------------- workflow rules
def test_submit_requires_gondi_text_and_audio(contributor):
    e = create_expr(contributor)
    r = contributor.post(f"/api/admin/expressions/{e['id']}/transition", json={"action": "submit"})
    assert r.status_code == 409 and "Gondi text" in r.json()["detail"]
    contributor.patch(f"/api/admin/expressions/{e['id']}", json={"gondi_text": PLACEHOLDER})
    r = contributor.post(f"/api/admin/expressions/{e['id']}/transition", json={"action": "submit"})
    assert r.status_code == 409 and "recording" in r.json()["detail"]


def test_approval_requires_speaker_consent(contributor, reviewer):
    e = create_expr(contributor, gondi_text=PLACEHOLDER)
    upload(contributor, e["id"])  # no speaker
    contributor.post(f"/api/admin/expressions/{e['id']}/transition", json={"action": "submit"})
    r = reviewer.post(f"/api/admin/expressions/{e['id']}/transition", json={"action": "approve"})
    assert r.status_code == 409 and "speaker" in r.json()["detail"].lower()


def test_reject_requires_comments_and_returns_to_draft(contributor, reviewer, consenting_speaker):
    e = create_expr(contributor, gondi_text=PLACEHOLDER)
    upload(contributor, e["id"], speaker_id=consenting_speaker)
    contributor.post(f"/api/admin/expressions/{e['id']}/transition", json={"action": "submit"})
    r = reviewer.post(f"/api/admin/expressions/{e['id']}/transition", json={"action": "reject"})
    assert r.status_code == 422
    r = reviewer.post(f"/api/admin/expressions/{e['id']}/transition",
                      json={"action": "reject", "comments": "Please re-record; background noise."})
    assert r.status_code == 200 and r.json()["status"] == "draft"
    assert r.json()["reviews"][0]["comments"].startswith("Please re-record")


def test_approve_and_publish_are_separate_roles(admin, contributor, reviewer, consenting_speaker):
    e = create_expr(contributor, gondi_text=PLACEHOLDER)
    upload(contributor, e["id"], speaker_id=consenting_speaker)
    contributor.post(f"/api/admin/expressions/{e['id']}/transition", json={"action": "submit"})
    # contributor cannot approve, reviewer cannot publish
    assert contributor.post(f"/api/admin/expressions/{e['id']}/transition", json={"action": "approve"}).status_code == 403
    # publish is not possible directly from review
    assert admin.post(f"/api/admin/expressions/{e['id']}/transition", json={"action": "publish"}).status_code == 409
    assert reviewer.post(f"/api/admin/expressions/{e['id']}/transition", json={"action": "approve"}).status_code == 200
    assert reviewer.post(f"/api/admin/expressions/{e['id']}/transition", json={"action": "publish"}).status_code == 403
    assert contributor.post(f"/api/admin/expressions/{e['id']}/transition", json={"action": "publish"}).status_code == 403
    assert admin.post(f"/api/admin/expressions/{e['id']}/transition", json={"action": "publish"}).status_code == 200


def test_editing_published_text_returns_to_draft(public, admin, contributor, reviewer, consenting_speaker):
    d = publish_flow(admin, contributor, reviewer, consenting_speaker)
    # contributor cannot edit a published entry
    assert contributor.patch(f"/api/admin/expressions/{d['id']}", json={"english": "Hi"}).status_code == 403
    # admin can, but a change to reviewed text sends it back to draft
    r = admin.patch(f"/api/admin/expressions/{d['id']}", json={"english": "Hello there"})
    assert r.status_code == 200 and r.json()["status"] == "draft"
    assert public.get("/api/public/expressions").json()["total"] == 0
    # changing only the category does not reset
    d2 = publish_flow(admin, contributor, reviewer, consenting_speaker, code="GON-002")
    cat = admin.post("/api/admin/categories", json={"name": "Greetings", "sort_order": 1}).json()
    r = admin.patch(f"/api/admin/expressions/{d2['id']}", json={"category_id": cat["id"]})
    assert r.json()["status"] == "published"
    assert public.get("/api/public/categories").json()[0]["name"] == "Greetings"


def test_live_default_recording_is_protected(admin, contributor, reviewer, consenting_speaker):
    d = publish_flow(admin, contributor, reviewer, consenting_speaker)
    rec = d["recordings"][0]
    assert admin.delete(f"/api/admin/recordings/{rec['id']}").status_code == 409
    assert admin.patch(f"/api/admin/recordings/{rec['id']}", json={"status": "rejected"}).status_code == 409
    assert admin.patch(f"/api/admin/recordings/{rec['id']}", json={"speaker_id": None}).status_code == 409


def test_duplicate_expression_code_rejected(contributor):
    create_expr(contributor, "GON-010")
    r = contributor.post("/api/admin/expressions", json={"expression_code": "gon-010", "english": "x"})
    assert r.status_code == 409


def test_archive_then_delete(admin, contributor):
    e = create_expr(contributor)
    assert admin.delete(f"/api/admin/expressions/{e['id']}").status_code == 409
    admin.post(f"/api/admin/expressions/{e['id']}/transition", json={"action": "archive"})
    assert admin.delete(f"/api/admin/expressions/{e['id']}").status_code == 204


# ----------------------------------------------------------------------------- audio validation
def test_rejects_non_audio_and_oversize(contributor, monkeypatch):
    e = create_expr(contributor)
    r = upload(contributor, e["id"], data=b"<?php echo 'hi'; ?>" + b"x" * 100, name="evil.mp3")
    assert r.status_code == 422
    r = upload(contributor, e["id"], data=make_wav(), name="clip.exe")
    assert r.status_code == 422
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "max_audio_bytes", 1000)
    r = upload(contributor, e["id"], data=make_wav())
    assert r.status_code == 422 and "limit" in r.json()["detail"]


def test_rejects_too_long_audio(contributor, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "max_audio_seconds", 1.0)
    e = create_expr(contributor)
    r = upload(contributor, e["id"], data=make_wav(seconds=2.0))
    assert r.status_code == 422 and "limit" in r.json()["detail"]


def test_browser_recording_is_converted_to_mp3(contributor):
    webm = ffmpeg_encode("webm")
    if webm is None:
        import pytest

        pytest.skip("ffmpeg not available")
    e = create_expr(contributor)
    r = upload(contributor, e["id"], data=webm, name="GON-001-mic.webm")
    assert r.status_code == 201, r.text
    rec = r.json()["recordings"][0]
    assert rec["mime_type"] == "audio/mpeg"
    assert rec["duration"] and 0.8 < rec["duration"] < 1.3
    audio = contributor.get(rec["preview_url"])
    assert audio.status_code == 200 and audio.headers["content-type"] == "audio/mpeg"


def test_mp3_upload(contributor):
    mp3 = ffmpeg_encode("mp3")
    if mp3 is None:
        import pytest

        pytest.skip("ffmpeg not available")
    e = create_expr(contributor)
    r = upload(contributor, e["id"], data=mp3, name="GON-001.mp3")
    assert r.status_code == 201, r.text
    assert r.json()["recordings"][0]["mime_type"] == "audio/mpeg"
