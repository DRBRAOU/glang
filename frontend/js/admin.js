/* Gondi Vaani — editorial console (vanilla JS). The backend enforces every permission;
   the UI only hides controls a role cannot use. */
(() => {
  "use strict";

  // ================================================================ utilities
  const $ = (s, root = document) => root.querySelector(s);
  const $$ = (s, root = document) => [...root.querySelectorAll(s)];

  /** Build a DOM element safely (text is never parsed as HTML). */
  function h(tag, attrs = {}, ...children) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "text") el.textContent = v;
      else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
      else if (k === "dataset") Object.assign(el.dataset, v);
      else el.setAttribute(k, v === true ? "" : v);
    }
    for (const c of children.flat()) {
      if (c === null || c === undefined || c === false) continue;
      el.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return el;
  }

  const STATUS_LABEL = {
    draft: "Draft", in_review: "In review", approved: "Approved", published: "Published", archived: "Archived",
    pending: "Pending", rejected: "Rejected",
  };
  const pill = (s) => h("span", { class: `status-pill s-${s}`, text: STATUS_LABEL[s] || s });
  const fmtDate = (iso) => (iso ? new Date(iso.endsWith("Z") ? iso : iso + "Z").toLocaleString() : "—");
  const fmtSize = (b) => (b > 1048576 ? `${(b / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1024))} KB`);

  let toastTimer;
  function toast(msg, isError = false) {
    const t = $("#toast");
    t.textContent = msg;
    t.classList.toggle("error", isError);
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.hidden = true; }, isError ? 6000 : 3000);
  }

  // ================================================================ API
  const session = { user: null, csrf: "" };

  class ApiError extends Error {
    constructor(status, message) { super(message); this.status = status; }
  }

  async function api(method, url, body, { form = false } = {}) {
    const opts = { method, credentials: "same-origin", headers: { Accept: "application/json" } };
    if (method !== "GET") opts.headers["X-CSRF-Token"] = session.csrf;
    if (body !== undefined) {
      if (form) opts.body = body;
      else { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
    }
    let res;
    try { res = await fetch(url, opts); } catch { throw new ApiError(0, "Network error — check your connection."); }
    if (res.status === 401 && !url.endsWith("/login")) { showLogin(); throw new ApiError(401, "Your session has ended. Please sign in again."); }
    if (res.status === 204) return null;
    let data = null;
    const ct = res.headers.get("content-type") || "";
    if (ct.includes("application/json")) data = await res.json();
    if (!res.ok) {
      const msg = (data && (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail))) || `Request failed (${res.status})`;
      throw new ApiError(res.status, msg);
    }
    return data;
  }

  const can = {
    edit: () => ["admin", "contributor"].includes(session.user?.role),
    review: () => ["admin", "reviewer"].includes(session.user?.role),
    admin: () => session.user?.role === "admin",
  };

  // ================================================================ auth
  function showLogin() {
    session.user = null;
    $("#app-view").hidden = true;
    $("#login-view").hidden = false;
    closeDrawer();
    setTimeout(() => $("#login-form [name=username]").focus(), 0);
  }

  function showApp() {
    $("#login-view").hidden = true;
    $("#app-view").hidden = false;
    const u = session.user;
    $("#account-btn").textContent = `${u.full_name || u.username} · ${u.role}`;
    for (const el of $$("[data-roles]")) el.hidden = !el.dataset.roles.split(" ").includes(u.role);
    const view = location.hash.slice(1);
    const tab = $(`#tabs [data-view="${view}"]`);
    showView(tab && !tab.hidden ? view : "expressions");
  }

  $("#login-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const f = ev.target;
    $("#login-error").textContent = "";
    try {
      const me = await api("POST", "/api/auth/login", { username: f.username.value, password: f.password.value });
      session.user = me; session.csrf = me.csrf_token;
      f.password.value = "";
      await loadLookups();
      showApp();
    } catch (err) {
      $("#login-error").textContent = err.message;
    }
  });

  $("#logout-btn").addEventListener("click", async () => {
    try { await api("POST", "/api/auth/logout"); } catch { /* ignore */ }
    showLogin();
  });

  $("#account-btn").addEventListener("click", () => {
    openModal({
      title: "Change your password",
      okLabel: "Change password",
      fields: [
        { name: "current_password", label: "Current password", type: "password", required: true, span2: true },
        { name: "new_password", label: "New password", type: "password", required: true, span2: true,
          hint: "At least 10 characters, with upper- and lower-case letters and a digit." },
      ],
      onSubmit: async (v) => {
        await api("POST", "/api/auth/password", v);
        const me = await api("GET", "/api/auth/me");
        session.csrf = me.csrf_token;
        toast("Password changed. Other sessions were signed out.");
      },
    });
  });

  // ================================================================ lookups
  const lookups = { categories: [], speakers: [] };
  async function loadLookups() {
    const [cats, spk] = await Promise.all([api("GET", "/api/admin/categories"), api("GET", "/api/admin/speakers")]);
    lookups.categories = cats;
    lookups.speakers = spk;
    const sel = $("#ex-cat"), cur = sel.value;
    sel.replaceChildren(h("option", { value: "", text: "All categories" }), ...cats.map((c) => h("option", { value: c.id, text: c.name })));
    sel.value = cur;
  }

  // ================================================================ views
  const loaders = {
    expressions: loadExpressions, review: loadReview, import: loadImportJobs, speakers: loadSpeakers,
    categories: loadCategories, users: loadUsers, reports: loadReports,
  };

  function showView(name) {
    for (const v of $$(".view")) v.hidden = v.id !== `view-${name}`;
    for (const b of $$("#tabs button")) {
      if (b.dataset.view === name) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
    }
    if (location.hash.slice(1) !== name) history.replaceState(null, "", `#${name}`);
    loaders[name]?.().catch((e) => toast(e.message, true));
    refreshReviewBadge();
  }
  for (const b of $$("#tabs button")) b.addEventListener("click", () => showView(b.dataset.view));

  async function refreshReviewBadge() {
    try {
      let n = (await api("GET", "/api/admin/expressions?status=in_review")).length;
      if (session.user.role === "admin") n += (await api("GET", "/api/admin/expressions?status=approved")).length;
      const b = $("#review-badge");
      b.textContent = n; b.hidden = n === 0;
    } catch { /* non-critical */ }
  }

  // ---------------------------------------------------------------- expressions list
  let exTimer;
  $("#ex-q").addEventListener("input", () => { clearTimeout(exTimer); exTimer = setTimeout(loadExpressions, 200); });
  $("#ex-status").addEventListener("change", loadExpressions);
  $("#ex-cat").addEventListener("change", loadExpressions);
  $("#new-expr-btn").addEventListener("click", () => openEditor(null));

  async function loadExpressions() {
    const p = new URLSearchParams({ q: $("#ex-q").value, status: $("#ex-status").value });
    if ($("#ex-cat").value) p.set("category_id", $("#ex-cat").value);
    const list = await api("GET", `/api/admin/expressions?${p}`);
    navIds = list.map((e) => e.id);
    const tbody = $("#ex-table tbody");
    tbody.replaceChildren(...list.map((e) => h("tr", { class: "clickable", tabindex: 0, onclick: () => openEditor(e.id),
      onkeydown: (ev) => { if (ev.key === "Enter") openEditor(e.id); } },
      h("td", { class: "code", text: e.expression_code }),
      e.gondi_text ? h("td", { class: "gondi", lang: "gon", text: e.gondi_text }) : h("td", { class: "empty-cell", text: "awaiting expert" }),
      h("td", { lang: "te", text: e.telugu }),
      h("td", { text: e.english }),
      h("td", { text: e.category || "—" }),
      h("td", { text: e.has_default_audio ? `✓ ${e.recordings}` : e.recordings ? `${e.recordings} (no default)` : "—" }),
      h("td", {}, pill(e.status)))));
    if (!list.length) tbody.append(h("tr", {}, h("td", { colspan: 7, class: "empty-cell", text: "No expressions match these filters." })));
    $("#ex-count").textContent = `${list.length} shown`;
  }

  // ---------------------------------------------------------------- review queue
  async function loadReview() {
    $("#review-intro").textContent = can.admin()
      ? "Reviewers approve entries after checking Gondi text, meanings, audio, speaker consent and attribution. Administrators then publish them."
      : can.review()
        ? "Open each entry, listen to the default recording, check the text, meanings, speaker and consent, then approve or send back with comments."
        : "Entries you submitted that are waiting for a reviewer or administrator.";
    const [inReview, approved] = await Promise.all([
      api("GET", "/api/admin/expressions?status=in_review"), api("GET", "/api/admin/expressions?status=approved")]);
    const fill = (sel, list, emptyText) => {
      const tb = $(`${sel} tbody`);
      tb.replaceChildren(...list.map((e) => h("tr", { class: "clickable", tabindex: 0, onclick: () => openEditor(e.id),
        onkeydown: (ev) => { if (ev.key === "Enter") openEditor(e.id); } },
        h("td", { class: "code", text: e.expression_code }),
        h("td", { class: "gondi", lang: "gon", text: e.gondi_text }),
        h("td", { text: [e.telugu, e.english].filter(Boolean).join(" · ") }),
        h("td", { text: fmtDate(e.updated_at) }))));
      if (!list.length) tb.append(h("tr", {}, h("td", { colspan: 4, class: "empty-cell", text: emptyText })));
    };
    navIds = [...inReview, ...approved].map((e) => e.id);
    fill("#rq-review", inReview, "Nothing is waiting for review.");
    fill("#rq-approved", approved, "Nothing is waiting to be published.");
  }

  // ================================================================ editor drawer
  let current = null; // ExpressionDetail
  let navIds = [];    // ids of the list the editor was opened from (for Previous / Next)
  const form = $("#ex-form");

  function openDrawer() {
    $("#drawer").hidden = false; $("#drawer-backdrop").hidden = false;
    document.body.style.overflow = "hidden";
    setTimeout(() => $("#drawer-close").focus(), 0);
  }
  function closeDrawer() {
    stopMic(true);
    $("#drawer").hidden = true; $("#drawer-backdrop").hidden = true;
    document.body.style.overflow = "";
    for (const a of $$("#drawer audio")) a.pause();
  }
  $("#drawer-close").addEventListener("click", () => { closeDrawer(); reloadActiveView(); });
  $("#drawer-backdrop").addEventListener("click", () => { closeDrawer(); reloadActiveView(); });
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && !$("#drawer").hidden && !$("#modal").open) { closeDrawer(); reloadActiveView(); }
  });
  function reloadActiveView() {
    const v = $$(".view").find((x) => !x.hidden);
    if (v) loaders[v.id.replace("view-", "")]?.().catch(() => {});
    refreshReviewBadge();
  }

  function fillCategorySelect() {
    const sel = form.category_id;
    sel.replaceChildren(h("option", { value: "", text: "—" }), ...lookups.categories.map((c) => h("option", { value: c.id, text: c.name })));
  }
  function fillSpeakerSelect(sel, selected) {
    sel.replaceChildren(h("option", { value: "", text: "— choose speaker —" }),
      ...lookups.speakers.filter((s) => s.active || s.id === selected).map((s) =>
        h("option", { value: s.id, text: `${s.display_name}${s.variety ? " · " + s.variety : ""}${s.has_consent ? "" : " (consent missing)"}` })));
    sel.value = selected ?? "";
  }

  async function openEditor(id) {
    fillCategorySelect();
    $("#ex-error").textContent = ""; $("#rec-error").textContent = ""; $("#wf-error").textContent = "";
    if (id === null) {
      current = null;
      form.reset();
      for (const el of form.elements) el.disabled = false;
      $("#drawer-title").textContent = "New expression";
      updateNav();
      $("#drawer-status").replaceChildren(pill("draft"));
      $("#ex-problems").hidden = true;
      $("#rec-block").hidden = true; $("#wf-block").hidden = true;
      $("#ex-save").hidden = false;
      $("#ex-save").textContent = "Create draft";
      openDrawer();
      form.expression_code.focus();
      return;
    }
    try {
      current = await api("GET", `/api/admin/expressions/${id}`);
    } catch (e) { toast(e.message, true); return; }
    renderEditor();
    openDrawer();
  }

  function updateNav() {
    const i = current ? navIds.indexOf(current.id) : -1;
    $("#drawer-prev").hidden = $("#drawer-next").hidden = i === -1;
    $("#drawer-pos").textContent = i === -1 ? "" : `${i + 1} / ${navIds.length}`;
    $("#drawer-prev").disabled = i <= 0;
    $("#drawer-next").disabled = i === -1 || i >= navIds.length - 1;
  }
  function go(delta) {
    const i = current ? navIds.indexOf(current.id) : -1;
    const id = navIds[i + delta];
    if (id === undefined) return;
    stopMic(true);
    for (const a of $$("#drawer audio")) a.pause();
    $("#mic-timer").textContent = "0:00";
    $("#rec-file").value = ""; $("#file-preview").hidden = true;
    $("#wf-comment").value = "";
    openEditor(id);
  }
  $("#drawer-prev").addEventListener("click", () => go(-1));
  $("#drawer-next").addEventListener("click", () => go(1));

  function renderEditor() {
    const e = current;
    updateNav();
    $("#drawer-title").textContent = e.expression_code;
    $("#drawer-status").replaceChildren(pill(e.status));
    for (const k of ["expression_code", "gondi_text", "transliteration", "telugu", "english", "variety", "script", "usage_note", "provenance"]) {
      form[k].value = e[k] ?? "";
    }
    form.category_id.value = e.category_id ?? "";
    for (const el of form.elements) el.disabled = !e.can_edit;
    $("#ex-save").hidden = !e.can_edit;
    $("#ex-save").textContent = ["approved", "published", "in_review"].includes(e.status) ? "Save (returns to draft if text changes)" : "Save";
    $("#variety-list").replaceChildren(...[...new Set(lookups.speakers.map((s) => s.variety).filter(Boolean))].map((v) => h("option", { value: v })));

    // publication readiness
    const pb = $("#ex-problems");
    if (e.status === "published") {
      pb.hidden = false; pb.className = "notice ok";
      pb.replaceChildren(h("strong", { text: "Live for learners. " }), `Published ${fmtDate(e.published_at)}.`);
    } else if (e.publication_problems.length && e.status !== "archived") {
      pb.hidden = false; pb.className = "notice";
      pb.replaceChildren(h("strong", { text: "Before this can be published:" }), h("ul", {}, e.publication_problems.map((p) => h("li", { text: p }))));
    } else {
      pb.hidden = true;
    }

    renderRecordings();
    renderWorkflow();
  }

  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    $("#ex-error").textContent = "";
    const body = {};
    for (const k of ["expression_code", "gondi_text", "transliteration", "telugu", "english", "variety", "script", "usage_note", "provenance"]) body[k] = form[k].value;
    body.category_id = form.category_id.value ? Number(form.category_id.value) : null;
    try {
      if (current) {
        current = await api("PATCH", `/api/admin/expressions/${current.id}`, body);
        toast("Saved.");
      } else {
        current = await api("POST", "/api/admin/expressions", body);
        toast("Draft created. Now add a recording.");
      }
      renderEditor();
    } catch (err) { $("#ex-error").textContent = err.message; }
  });

  // ---------------------------------------------------------------- recordings
  function renderRecordings() {
    const e = current;
    $("#rec-block").hidden = false;
    const list = $("#rec-list");
    const editableEntry = e.status !== "archived";
    const editorHere = can.admin() || (session.user.role === "contributor" && e.status === "draft");
    list.replaceChildren(...e.recordings.map((r) => {
      const spSel = h("select", { "aria-label": "Speaker", disabled: !(editableEntry && (editorHere || can.review())) || null });
      fillSpeakerSelect(spSel, r.speaker_id);
      spSel.addEventListener("change", () => patchRecording(r.id, { speaker_id: spSel.value ? Number(spSel.value) : null }));
      const audio = h("audio", { controls: true, preload: "metadata", src: r.preview_url });
      return h("li", { class: `rec${r.is_default ? " is-default" : ""}` },
        h("div", { class: "rec-top" },
          r.is_default ? h("span", { class: "default-tag", text: "DEFAULT" }) : null,
          pill(r.status),
          h("span", { class: "fn", text: r.original_filename || `Recording ${r.id}` }),
          h("span", { class: "muted", text: `${r.duration ? r.duration.toFixed(1) + " s · " : ""}${fmtSize(r.size_bytes)} · ${fmtDate(r.created_at)}` })),
        audio,
        h("div", { class: "rec-ctrls" },
          spSel,
          !r.is_default && editableEntry && (editorHere || can.review())
            ? h("button", { type: "button", class: "btn small", text: "Make default", onclick: () => patchRecording(r.id, { is_default: true }) }) : null,
          can.review() && editableEntry && r.status !== "approved"
            ? h("button", { type: "button", class: "btn small good", text: "Approve audio", onclick: () => patchRecording(r.id, { status: "approved" }) }) : null,
          can.review() && editableEntry && r.status !== "rejected"
            ? h("button", { type: "button", class: "btn small danger", text: "Reject audio", onclick: () => patchRecording(r.id, { status: "rejected" }) }) : null,
          (can.admin() || (session.user.role === "contributor" && e.status === "draft" && r.status !== "approved"))
            ? h("button", { type: "button", class: "btn small ghost danger", text: "Delete", onclick: () => deleteRecording(r) }) : null));
    }));
    if (!e.recordings.length) list.append(h("li", { class: "muted small", text: "No recordings yet." }));

    const canAdd = editableEntry && (can.admin() || (session.user.role === "contributor" && e.status === "draft"));
    $("#add-rec").hidden = !canAdd;
    if (canAdd) {
      const pr = $("#rec-prompt");
      pr.replaceChildren(...[h("span", { class: "p-label", text: `Prompt · ${e.expression_code}` }),
        e.telugu ? h("span", { class: "p-te", lang: "te", text: e.telugu }) : null,
        e.english ? h("span", { class: "p-en", text: e.english }) : null,
        e.gondi_text ? h("span", { class: "p-note", lang: "gon", text: `Gondi: ${e.gondi_text}` }) : null
      ].filter(Boolean));
      fillSpeakerSelect($("#rec-speaker"), $("#rec-speaker").value ? Number($("#rec-speaker").value) : null);
      $("#rec-default").checked = ["draft", "in_review"].includes(e.status);
      $("#rec-default").disabled = !["draft", "in_review"].includes(e.status);
      setupMicNote();
    }
  }

  async function patchRecording(id, body) {
    $("#rec-error").textContent = "";
    try { current = await api("PATCH", `/api/admin/recordings/${id}`, body); renderEditor(); toast("Recording updated."); }
    catch (err) { $("#rec-error").textContent = err.message; renderEditor(); }
  }

  async function deleteRecording(r) {
    if (!confirmAction(`Delete recording “${r.original_filename || r.id}”? This cannot be undone.`)) return;
    try { current = await api("DELETE", `/api/admin/recordings/${r.id}`); renderEditor(); toast("Recording deleted."); }
    catch (err) { $("#rec-error").textContent = err.message; }
  }

  function confirmAction(msg) { return window.confirm(msg); } // eslint-disable-line no-alert

  async function uploadRecording(blob, filename) {
    $("#rec-error").textContent = "";
    const fd = new FormData();
    fd.append("file", blob, filename);
    if ($("#rec-speaker").value) fd.append("speaker_id", $("#rec-speaker").value);
    fd.append("make_default", $("#rec-default").checked ? "true" : "false");
    try {
      current = await api("POST", `/api/admin/expressions/${current.id}/recordings`, fd, { form: true });
      renderEditor();
      toast("Recording saved.");
      return true;
    } catch (err) { $("#rec-error").textContent = err.message; return false; }
  }

  // file upload with preview
  let fileUrl = null;
  $("#rec-file").addEventListener("change", () => {
    const f = $("#rec-file").files[0];
    if (fileUrl) URL.revokeObjectURL(fileUrl);
    if (!f) { $("#file-preview").hidden = true; return; }
    fileUrl = URL.createObjectURL(f);
    $("#file-audio").src = fileUrl;
    $("#file-preview").hidden = false;
  });
  $("#file-save").addEventListener("click", async () => {
    const f = $("#rec-file").files[0];
    if (!f) return;
    $("#file-save").disabled = true;
    if (await uploadRecording(f, f.name)) { $("#rec-file").value = ""; $("#file-preview").hidden = true; }
    $("#file-save").disabled = false;
  });

  // ---------------------------------------------------------------- microphone recorder
  const mic = { stream: null, rec: null, chunks: [], blob: null, mime: "", started: 0, tick: null, ctx: null, raf: null, url: null };
  const MAX_SECONDS = 120;

  function setupMicNote() {
    const note = $("#mic-note");
    if (!window.isSecureContext) {
      note.textContent = "Microphone recording needs HTTPS. Open this console through the https:// address.";
      $("#mic-start").disabled = true;
    } else if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
      note.textContent = "This browser cannot record audio. Use a current Chrome, Edge, Firefox or Safari, or upload a file.";
      $("#mic-start").disabled = true;
    } else {
      note.textContent = "Use a quiet room and keep the microphone about 15 cm from the speaker. You can listen before saving.";
      $("#mic-start").disabled = false;
    }
  }

  function pickMime() {
    const prefs = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus", "audio/ogg"];
    return prefs.find((m) => MediaRecorder.isTypeSupported?.(m)) || "";
  }

  $("#mic-start").addEventListener("click", async () => {
    $("#rec-error").textContent = "";
    try {
      mic.stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: true, channelCount: 1 },
      });
    } catch (err) {
      $("#rec-error").textContent = err.name === "NotAllowedError"
        ? "Microphone permission was denied. Allow microphone access for this site in the browser settings."
        : `Could not open the microphone (${err.name}).`;
      return;
    }
    mic.mime = pickMime();
    mic.chunks = [];
    mic.rec = new MediaRecorder(mic.stream, mic.mime ? { mimeType: mic.mime, audioBitsPerSecond: 128000 } : undefined);
    mic.rec.addEventListener("dataavailable", (ev) => { if (ev.data.size) mic.chunks.push(ev.data); });
    mic.rec.addEventListener("stop", onMicStopped);
    mic.rec.start(250);
    mic.started = performance.now();
    $("#mic-start").hidden = true; $("#mic-stop").hidden = false; $("#mic-preview").hidden = true;
    mic.tick = setInterval(() => {
      const s = Math.floor((performance.now() - mic.started) / 1000);
      $("#mic-timer").textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
      if (s >= MAX_SECONDS) stopMic();
    }, 250);
    drawLevel();
  });
  $("#mic-stop").addEventListener("click", () => stopMic());

  function drawLevel() {
    try {
      mic.ctx = new (window.AudioContext || window.webkitAudioContext)();
      const src = mic.ctx.createMediaStreamSource(mic.stream);
      const an = mic.ctx.createAnalyser();
      an.fftSize = 512;
      src.connect(an);
      const data = new Uint8Array(an.fftSize);
      const cv = $("#mic-level"), g = cv.getContext("2d");
      const color = getComputedStyle(document.documentElement).getPropertyValue("--clay").trim() || "#a6461f";
      const loop = () => {
        an.getByteTimeDomainData(data);
        let peak = 0;
        for (const v of data) peak = Math.max(peak, Math.abs(v - 128) / 128);
        g.clearRect(0, 0, cv.width, cv.height);
        g.fillStyle = peak > 0.95 ? "#c62828" : color;
        g.fillRect(0, 0, Math.min(1, peak * 1.4) * cv.width, cv.height);
        mic.raf = requestAnimationFrame(loop);
      };
      loop();
    } catch { /* level meter is optional */ }
  }

  function stopMic(discard = false) {
    clearInterval(mic.tick);
    cancelAnimationFrame(mic.raf);
    if (mic.ctx) { mic.ctx.close().catch(() => {}); mic.ctx = null; }
    if (mic.rec && mic.rec.state !== "inactive") { mic.discard = discard; mic.rec.stop(); }
    if (mic.stream) { mic.stream.getTracks().forEach((t) => t.stop()); mic.stream = null; }
    $("#mic-start").hidden = false; $("#mic-stop").hidden = true;
    if (discard) { $("#mic-preview").hidden = true; mic.blob = null; }
  }

  function onMicStopped() {
    if (mic.discard) { mic.discard = false; return; }
    const type = (mic.rec.mimeType || mic.mime || "audio/webm").split(";")[0];
    mic.blob = new Blob(mic.chunks, { type });
    if (mic.url) URL.revokeObjectURL(mic.url);
    mic.url = URL.createObjectURL(mic.blob);
    $("#mic-audio").src = mic.url;
    $("#mic-preview").hidden = false;
  }
  $("#mic-discard").addEventListener("click", () => { $("#mic-preview").hidden = true; mic.blob = null; $("#mic-timer").textContent = "0:00"; });
  $("#mic-save").addEventListener("click", async () => {
    if (!mic.blob) return;
    const ext = { "audio/webm": "webm", "audio/mp4": "m4a", "audio/ogg": "ogg" }[mic.blob.type] || "webm";
    $("#mic-save").disabled = true;
    const ok = await uploadRecording(mic.blob, `${current.expression_code}-mic-${Date.now()}.${ext}`);
    $("#mic-save").disabled = false;
    if (ok) { $("#mic-preview").hidden = true; mic.blob = null; $("#mic-timer").textContent = "0:00"; }
  });

  // ---------------------------------------------------------------- workflow
  const ACTIONS = {
    submit: { label: "Submit for review", cls: "primary" },
    approve: { label: "Approve (text + default audio)", cls: "good" },
    reject: { label: "Send back for revision", cls: "danger", needsComment: true },
    publish: { label: "Publish to learners", cls: "good" },
    unpublish: { label: "Unpublish", cls: "" },
    archive: { label: "Archive", cls: "ghost danger", confirm: "Archive this expression? It will disappear from the learner library." },
    restore: { label: "Restore as draft", cls: "" },
  };
  const ORDER = ["draft", "in_review", "approved", "published"];

  function renderWorkflow() {
    const e = current;
    $("#wf-block").hidden = false;
    const idx = ORDER.indexOf(e.status);
    for (const li of $$("#flow li")) {
      const i = ORDER.indexOf(li.dataset.s);
      li.className = i < idx ? "done" : i === idx ? "now" : "";
    }
    const wrap = $("#wf-actions");
    wrap.replaceChildren(...e.allowed_actions.map((a) => h("button", {
      type: "button", class: `btn ${ACTIONS[a]?.cls || ""}`, text: ACTIONS[a]?.label || a, onclick: () => doTransition(a),
    })));
    $("#wf-comment-wrap").hidden = !e.allowed_actions.length;
    if (!e.allowed_actions.length) wrap.append(h("p", { class: "muted small", text: "No workflow actions are available to your role at this stage." }));

    $("#history").replaceChildren(...e.reviews.map((r) => h("li", {},
      h("div", {}, h("strong", { text: r.decision.replace("_", " ") }), " · ", r.reviewer || "system", " ",
        h("span", { class: "when", text: fmtDate(r.timestamp) })),
      r.comments ? h("div", { class: "c", text: r.comments }) : null)));
  }

  async function doTransition(action) {
    $("#wf-error").textContent = "";
    const comments = $("#wf-comment").value.trim();
    const meta = ACTIONS[action] || {};
    if (meta.needsComment && !comments) { $("#wf-error").textContent = "Please write what needs to be revised."; $("#wf-comment").focus(); return; }
    if (meta.confirm && !confirmAction(meta.confirm)) return;
    try {
      current = await api("POST", `/api/admin/expressions/${current.id}/transition`, { action, comments });
      $("#wf-comment").value = "";
      renderEditor();
      toast(`${meta.label || action}: done.`);
      refreshReviewBadge();
    } catch (err) { $("#wf-error").textContent = err.message; }
  }

  // ================================================================ import
  $("#import-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    $("#import-error").textContent = "";
    const f = ev.target;
    const fd = new FormData();
    fd.append("csv_file", f.csv_file.files[0]);
    if (f.zip_file.files[0]) fd.append("zip_file", f.zip_file.files[0]);
    fd.append("mode", f.mode.value);
    const btn = f.querySelector("button");
    btn.disabled = true; btn.textContent = "Checking…";
    try {
      const job = await api("POST", "/api/admin/imports/preview", fd, { form: true });
      renderImport(job);
      loadImportJobs();
    } catch (err) { $("#import-error").textContent = err.message; }
    finally { btn.disabled = false; btn.textContent = "Check files (dry run)"; }
  });

  function renderImport(job) {
    const box = $("#import-result");
    box.hidden = false;
    const r = job.report || {};
    const rows = r.rows || [];
    const skip = h("input", { type: "checkbox", id: "skip-invalid" });
    const commitBtn = h("button", { type: "button", class: "btn primary",
      text: job.rejected ? `Import ${job.accepted} valid rows` : `Import all ${job.accepted} rows`,
      disabled: (job.status !== "previewed" || !job.accepted || job.rejected > 0) || null });
    skip.addEventListener("change", () => { commitBtn.disabled = !skip.checked || !job.accepted; });
    commitBtn.addEventListener("click", async () => {
      commitBtn.disabled = true; commitBtn.textContent = "Importing…";
      try {
        const done = await api("POST", `/api/admin/imports/${job.id}/commit`, { skip_invalid: skip.checked });
        toast(`${done.accepted} rows imported.`);
        renderImport(done);
        loadImportJobs(); loadLookups();
      } catch (err) { toast(err.message, true); commitBtn.disabled = false; commitBtn.textContent = "Try again"; }
    });
    const discardBtn = h("button", { type: "button", class: "btn ghost", text: "Discard preview", onclick: async () => {
      await api("DELETE", `/api/admin/imports/${job.id}`); box.hidden = true; loadImportJobs();
    } });

    const sorted = [...rows].sort((a, b) => (a.ok === b.ok ? a.line - b.line : a.ok ? 1 : -1));
    box.replaceChildren(
      h("div", { class: "panel" },
        h("h3", { text: `Import #${job.id}: ${job.filename}${job.zip_filename ? " + " + job.zip_filename : ""}` }),
        h("div", { class: "import-summary" },
          h("div", { class: "stat" }, h("b", { text: job.total }), h("span", { text: "rows" })),
          h("div", { class: "stat" }, h("b", { text: job.accepted }), h("span", { text: "valid" })),
          r.mode === "update" ? h("div", { class: "stat" }, h("b", { text: r.creates ?? 0 }), h("span", { text: "new" })) : null,
          r.mode === "update" ? h("div", { class: "stat" }, h("b", { text: r.updates ?? 0 }), h("span", { text: "updates" })) : null,
          h("div", { class: `stat${job.rejected ? " warn" : ""}` }, h("b", { text: job.rejected }), h("span", { text: "with errors" })),
          h("div", { class: "muted", text: `Status: ${job.status}` })),
        (r.warnings || []).length ? h("div", { class: "notice" }, h("ul", {}, r.warnings.map((w) => h("li", { text: w })))) : null,
        (r.unused_audio || []).length ? h("p", { class: "small muted", text: `Unused audio files: ${r.unused_audio.slice(0, 20).join(", ")}${r.unused_audio.length > 20 ? "…" : ""}` }) : null,
        job.status === "previewed" ? h("div", { class: "import-actions" },
          job.rejected ? h("label", { class: "row" }, skip, " Skip the rows with errors and import only valid rows") : null,
          commitBtn, discardBtn) : null,
        h("div", { class: "table-wrap" }, h("table", { class: "grid compact" },
          h("thead", {}, h("tr", {}, ["Line", "ID", "Action", "English", "Telugu", "Audio", "Result"].map((t) => h("th", { text: t })))),
          h("tbody", {}, sorted.map((row) => h("tr", { class: row.ok ? "" : "bad" },
            h("td", { text: row.line }), h("td", { class: "code", text: row.expression_id }),
            h("td", { class: "small", text: row.action === "update" ? `update${row.changes?.length ? ": " + row.changes.join(", ") : ""}` : "new" }),
            h("td", { text: row.english }), h("td", { lang: "te", text: row.telugu }),
            h("td", { text: row.audio_filename + (row.duration ? ` (${row.duration}s)` : "") }),
            h("td", {}, row.ok ? h("span", { text: "✓ OK" }) : null,
              ...row.errors.map((x) => h("div", { class: "row-err", text: "✗ " + x })),
              ...row.warnings.map((x) => h("div", { class: "row-warn small", text: "! " + x }))))))))));
    box.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  async function loadImportJobs() {
    const jobs = await api("GET", "/api/admin/imports");
    const tb = $("#import-jobs tbody");
    tb.replaceChildren(...jobs.map((j) => h("tr", { class: "clickable", onclick: async () => renderImport(await api("GET", `/api/admin/imports/${j.id}`)) },
      h("td", { text: j.id }), h("td", { text: j.filename }), h("td", { text: j.zip_filename || "—" }),
      h("td", { text: j.total }), h("td", { text: j.accepted }), h("td", { text: j.status }), h("td", { text: fmtDate(j.timestamp) }))));
    if (!jobs.length) tb.append(h("tr", {}, h("td", { colspan: 7, class: "empty-cell", text: "No imports yet." })));
  }

  // ================================================================ speakers
  $("#new-speaker-btn").addEventListener("click", () => speakerModal(null));
  async function loadSpeakers() {
    await loadLookups();
    const tb = $("#sp-table tbody");
    tb.replaceChildren(...lookups.speakers.map((s) => h("tr", {},
      h("td", {}, h("strong", { text: s.display_name }), s.active ? null : h("span", { class: "muted", text: " (inactive)" })),
      h("td", { text: s.variety || "—" }),
      h("td", { text: s.attribution || "—" }),
      h("td", {}, s.has_consent ? h("span", { text: `✓ ${s.consent_date}` }) : h("span", { class: "row-err", text: "Not recorded" }),
        s.consent_scope ? h("div", { class: "small muted", text: s.consent_scope }) : null),
      h("td", { class: "small", text: s.reuse_restrictions || "—" }),
      h("td", {}, can.admin() ? h("button", { type: "button", class: "btn small", text: "Edit", onclick: () => speakerModal(s) }) : null))));
    if (!lookups.speakers.length) tb.append(h("tr", {}, h("td", { colspan: 6, class: "empty-cell", text: "No speakers registered yet." })));
  }

  function speakerModal(s) {
    openModal({
      title: s ? `Edit speaker: ${s.display_name}` : "Add speaker",
      fields: [
        { name: "display_name", label: "Name (internal)", required: true, value: s?.display_name },
        { name: "variety", label: "Gondi variety / region", value: s?.variety },
        { name: "attribution", label: "Credit line shown to learners", value: s?.attribution, span2: true,
          hint: "e.g. “Smt. … , Utnoor”. Leave blank to show the name." },
        { name: "consent_scope", label: "Consent scope", type: "textarea", value: s?.consent_scope, span2: true,
          hint: "What the speaker agreed to, e.g. “Educational use in BRAOU courses and the Gondi Vaani website.”" },
        { name: "consent_date", label: "Consent date", type: "date", value: s?.consent_date },
        { name: "active", label: "Active", type: "checkbox", value: s ? s.active : true },
        { name: "reuse_restrictions", label: "Re-use restrictions", type: "textarea", value: s?.reuse_restrictions, span2: true },
        { name: "notes", label: "Internal notes", type: "textarea", value: s?.notes, span2: true },
      ],
      onSubmit: async (v) => {
        v.consent_date = v.consent_date || null;
        if (s) await api("PUT", `/api/admin/speakers/${s.id}`, v); else await api("POST", "/api/admin/speakers", v);
        toast("Speaker saved.");
        await loadSpeakers();
        if (current && !$("#drawer").hidden) renderEditor();
      },
    });
  }

  // ================================================================ categories
  $("#cat-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const f = ev.target;
    try {
      await api("POST", "/api/admin/categories", { name: f.name.value, sort_order: Number(f.sort_order.value || 0) });
      f.name.value = "";
      toast("Category added.");
      loadCategories();
    } catch (err) { toast(err.message, true); }
  });
  async function loadCategories() {
    await loadLookups();
    const tb = $("#cat-table tbody");
    tb.replaceChildren(...lookups.categories.map((c) => h("tr", {},
      h("td", { text: c.name }), h("td", { text: c.sort_order }),
      h("td", {}, can.admin() ? h("div", { class: "row" },
        h("button", { type: "button", class: "btn small", text: "Edit", onclick: () => openModal({
          title: "Edit category",
          fields: [{ name: "name", label: "Name", required: true, value: c.name }, { name: "sort_order", label: "Sort order", type: "number", value: c.sort_order }],
          onSubmit: async (v) => { await api("PUT", `/api/admin/categories/${c.id}`, { name: v.name, sort_order: Number(v.sort_order || 0) }); loadCategories(); },
        }) }),
        h("button", { type: "button", class: "btn small ghost danger", text: "Delete", onclick: async () => {
          if (!confirmAction(`Delete category “${c.name}”?`)) return;
          try { await api("DELETE", `/api/admin/categories/${c.id}`); loadCategories(); } catch (err) { toast(err.message, true); }
        } })) : null))));
  }

  // ================================================================ users
  $("#new-user-btn").addEventListener("click", () => openModal({
    title: "Add user",
    fields: [
      { name: "username", label: "Username", required: true },
      { name: "full_name", label: "Full name" },
      { name: "role", label: "Role", type: "select", options: [["contributor", "Contributor"], ["reviewer", "Reviewer"], ["admin", "Administrator"]], value: "contributor" },
      { name: "password", label: "Initial password", type: "password", required: true, hint: "10+ characters with upper/lower case and a digit. Ask the user to change it." },
    ],
    onSubmit: async (v) => { await api("POST", "/api/admin/users", v); toast("User created."); loadUsers(); },
  }));
  async function loadUsers() {
    const users = await api("GET", "/api/admin/users");
    $("#user-table tbody").replaceChildren(...users.map((u) => h("tr", {},
      h("td", { class: "code", text: u.username }), h("td", { text: u.full_name || "—" }), h("td", { text: u.role }),
      h("td", { text: u.active ? "Active" : "Disabled" }), h("td", { text: fmtDate(u.last_login_at) }),
      h("td", {}, h("button", { type: "button", class: "btn small", text: "Edit", onclick: () => openModal({
        title: `Edit ${u.username}`,
        fields: [
          { name: "full_name", label: "Full name", value: u.full_name },
          { name: "role", label: "Role", type: "select", options: [["contributor", "Contributor"], ["reviewer", "Reviewer"], ["admin", "Administrator"]], value: u.role },
          { name: "active", label: "Account active", type: "checkbox", value: u.active },
          { name: "password", label: "Reset password (optional)", type: "password", hint: "Leave blank to keep the current password." },
        ],
        onSubmit: async (v) => { if (!v.password) delete v.password; await api("PATCH", `/api/admin/users/${u.id}`, v); toast("User updated."); loadUsers(); },
      }) })))));
  }

  // ================================================================ reports
  $("#backup-btn").addEventListener("click", async () => {
    $("#backup-btn").disabled = true;
    try { const b = await api("POST", "/api/admin/backups"); toast(`Backup created: ${b.name}`); loadBackups(); }
    catch (err) { toast(err.message, true); }
    finally { $("#backup-btn").disabled = false; }
  });
  async function loadBackups() {
    const list = await api("GET", "/api/admin/backups");
    $("#backup-list").replaceChildren(...list.slice(0, 10).map((b) => h("li", {},
      h("a", { href: `/api/admin/backups/${encodeURIComponent(b.name)}`, download: b.name, text: b.name }),
      h("span", { class: "muted", text: ` · ${fmtSize(b.size_bytes)}` }))));
    if (!list.length) $("#backup-list").append(h("li", { class: "muted", text: "No backups yet." }));
  }
  async function loadReports() {
    const s = await api("GET", "/api/admin/reports/summary");
    const st = s.expressions_by_status;
    const stat = (n, label, warn) => h("div", { class: `stat${warn && n ? " warn" : ""}` }, h("b", { text: n ?? 0 }), h("span", { text: label }));
    $("#stats").replaceChildren(
      stat(st.published, "Published"), stat(st.approved, "Approved, not yet published"), stat(st.in_review, "In review"),
      stat(st.draft, "Drafts"), stat(s.missing_gondi_text, "Missing Gondi text", true), stat(s.without_audio, "Without audio", true),
      stat(s.recordings_by_status.pending, "Recordings pending"), stat(s.speakers_without_consent, "Speakers without consent", true),
      stat(s.approved_audio_minutes, "Minutes of approved audio"));
    $("#cat-report tbody").replaceChildren(...s.expressions_by_category.map((c) => h("tr", {}, h("td", { text: c.category }), h("td", { text: c.total }))));
    if (can.admin()) {
      loadBackups();
      const log = await api("GET", "/api/admin/audit?limit=200");
      $("#audit-table tbody").replaceChildren(...log.map((a) => h("tr", {},
        h("td", { text: a.at.replace("T", " ") }), h("td", { text: a.user }), h("td", { text: a.action }),
        h("td", { text: a.entity ? `${a.entity} ${a.entity_id}` : "" }), h("td", { class: "small", text: a.detail }))));
    }
  }

  // ================================================================ generic modal
  function openModal({ title, fields, okLabel = "Save", onSubmit }) {
    const dlg = $("#modal");
    $("#modal-title").textContent = title;
    $("#modal-ok").textContent = okLabel;
    $("#modal-error").textContent = "";
    const body = $("#modal-body");
    body.replaceChildren(...fields.map((f) => {
      let input;
      if (f.type === "textarea") input = h("textarea", { name: f.name, rows: 2 });
      else if (f.type === "select") input = h("select", { name: f.name }, f.options.map(([v, t]) => h("option", { value: v, text: t })));
      else input = h("input", { name: f.name, type: f.type || "text", required: f.required || null,
        autocomplete: f.type === "password" ? "new-password" : "off" });
      if (f.type === "checkbox") input.checked = !!f.value;
      else if (f.value !== undefined && f.value !== null) input.value = f.value;
      if (f.type === "checkbox") return h("label", { class: "check" }, input, f.label);
      return h("label", { class: f.span2 ? "span2" : "" }, f.label, input, f.hint ? h("small", { text: f.hint }) : null);
    }));
    const submit = async (ev) => {
      ev.preventDefault();
      const v = {};
      for (const f of fields) {
        const el = body.querySelector(`[name="${f.name}"]`);
        v[f.name] = f.type === "checkbox" ? el.checked : el.value;
      }
      $("#modal-ok").disabled = true;
      try { await onSubmit(v); dlg.close(); }
      catch (err) { $("#modal-error").textContent = err.message; }
      finally { $("#modal-ok").disabled = false; }
    };
    const formEl = $("#modal-form");
    formEl.onsubmit = submit;
    $("#modal-cancel").onclick = () => dlg.close();
    dlg.showModal();
    body.querySelector("input, textarea, select")?.focus();
  }

  // ================================================================ boot
  (async function boot() {
    try {
      const me = await api("GET", "/api/auth/me");
      session.user = me; session.csrf = me.csrf_token;
      await loadLookups();
      showApp();
    } catch {
      showLogin();
    }
  })();
})();
