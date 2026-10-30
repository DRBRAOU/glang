/* Gondi Vaani — learner library (vanilla JS, no dependencies) */
(() => {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const els = {
    q: $("#q"), clear: $("#clear-search"), form: $("#search-form"), chips: $("#chips"), favOnly: $("#fav-only"),
    count: $("#result-count"), cards: $("#cards"), empty: $("#empty"), emptyTitle: $("#empty-title"),
    emptyBody: $("#empty-body"), loading: $("#loading"), tpl: $("#card-tpl"), audio: $("#audio"),
    player: $("#player"), nowGondi: $("#now-gondi"), nowMeaning: $("#now-meaning"), playpause: $("#playpause"),
    prev: $("#prev"), next: $("#next"), replay: $("#replay"), bar: $("#bar"), announcer: $("#announcer"),
  };

  const store = {
    get(key, fallback) {
      try { const v = localStorage.getItem(key); return v === null ? fallback : JSON.parse(v); } catch { return fallback; }
    },
    set(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode */ } },
  };

  const state = {
    items: [],
    categories: [],
    category: null,          // category id or null for all
    query: "",
    visible: [],             // filtered items
    current: null,           // expression id currently loaded in the player
    rate: store.get("gv:rate", 1),
    favs: new Set(store.get("gv:favs", [])),
  };

  // ------------------------------------------------------------------ helpers
  const norm = (s) => (s || "").normalize("NFC").toLocaleLowerCase().replace(/\s+/g, " ").trim();
  const announce = (msg) => { els.announcer.textContent = ""; setTimeout(() => { els.announcer.textContent = msg; }, 30); };

  async function getJSON(url) {
    const res = await fetch(url, { headers: { Accept: "application/json" }, credentials: "same-origin" });
    if (!res.ok) throw new Error(`Request failed (${res.status})`);
    return res.json();
  }

  // ------------------------------------------------------------------ filters
  function renderChips() {
    els.chips.textContent = "";
    const all = [{ id: null, name: "All · అన్నీ", count: state.items.length }, ...state.categories];
    for (const c of all) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "chip";
      b.setAttribute("role", "listitem");
      b.setAttribute("aria-pressed", String(state.category === c.id));
      b.textContent = c.name;
      const n = document.createElement("span");
      n.className = "n";
      n.textContent = c.count;
      b.append(n);
      b.addEventListener("click", () => { state.category = c.id; renderChips(); applyFilters(); });
      els.chips.append(b);
    }
  }

  function applyFilters() {
    const q = norm(state.query);
    state.visible = state.items.filter((e) => {
      if (state.category && e.category_id !== state.category) return false;
      if (els.favOnly.checked && !state.favs.has(e.code)) return false;
      if (!q) return true;
      return e._haystack.includes(q);
    });
    renderCards();
  }

  // ------------------------------------------------------------------ cards
  function renderCards() {
    els.cards.textContent = "";
    const frag = document.createDocumentFragment();
    for (const e of state.visible) frag.append(buildCard(e));
    els.cards.append(frag);

    const n = state.visible.length;
    els.count.textContent = state.items.length
      ? `${n} of ${state.items.length} expression${state.items.length === 1 ? "" : "s"}`
      : "";
    els.empty.hidden = n > 0;
    if (!n) {
      if (!state.items.length) {
        els.emptyTitle.textContent = "The library is being prepared.";
        els.emptyBody.textContent = "Expert-reviewed recordings will appear here once they are published.";
      } else if (els.favOnly.checked && !state.favs.size) {
        els.emptyTitle.textContent = "No favourites yet.";
        els.emptyBody.textContent = "Tap ★ on any expression to keep it here for practice.";
      } else {
        els.emptyTitle.textContent = "No expressions match.";
        els.emptyBody.textContent = "Try a different word, or choose “All”.";
      }
    }
    syncPlayingCard();
  }

  function buildCard(e) {
    const li = els.tpl.content.firstElementChild.cloneNode(true);
    li.dataset.id = e.id;
    li.id = `x-${e.code}`;
    li.querySelector(".gondi").textContent = e.gondi_text;
    li.querySelector(".translit").textContent = e.transliteration;
    const te = li.querySelector(".m-te"), en = li.querySelector(".m-en");
    if (e.telugu) te.querySelector("dd").textContent = e.telugu; else te.remove();
    if (e.english) en.querySelector("dd").textContent = e.english; else en.remove();
    li.querySelector(".note").textContent = e.usage_note || "";
    const meta = li.querySelector(".meta");
    const bits = [e.category, e.variety && `Variety: ${e.variety}`, e.speaker && (/^voice\s*:/i.test(e.speaker) ? e.speaker : `Voice: ${e.speaker}`),
      e.duration ? `${e.duration.toFixed(1)} s` : ""].filter(Boolean);
    for (const b of bits) { const s = document.createElement("span"); s.textContent = b; meta.append(s); }

    const label = e.english || e.telugu || e.gondi_text;
    const play = li.querySelector(".play");
    play.setAttribute("aria-label", `Play: ${label}`);
    play.addEventListener("click", () => toggle(e.id));

    const fav = li.querySelector(".fav");
    const isFav = state.favs.has(e.code);
    fav.setAttribute("aria-pressed", String(isFav));
    fav.setAttribute("aria-label", isFav ? `Remove from favourites: ${label}` : `Add to favourites: ${label}`);
    fav.addEventListener("click", () => {
      if (state.favs.has(e.code)) state.favs.delete(e.code); else state.favs.add(e.code);
      store.set("gv:favs", [...state.favs]);
      const on = state.favs.has(e.code);
      fav.setAttribute("aria-pressed", String(on));
      fav.setAttribute("aria-label", on ? `Remove from favourites: ${label}` : `Add to favourites: ${label}`);
      announce(on ? "Added to favourites" : "Removed from favourites");
      if (els.favOnly.checked) applyFilters();
    });
    return li;
  }

  function syncPlayingCard() {
    const playing = !els.audio.paused && !els.audio.ended;
    for (const li of els.cards.children) {
      const isCur = Number(li.dataset.id) === state.current;
      li.classList.toggle("current", isCur);
      li.classList.toggle("playing", isCur && playing);
    }
    els.player.classList.toggle("playing", playing);
    els.playpause.setAttribute("aria-label", playing ? "Pause" : "Play");
  }

  // ------------------------------------------------------------------ playback
  const byId = (id) => state.items.find((e) => e.id === id);

  function load(id, autoplay = true) {
    const e = byId(id);
    if (!e) return;
    state.current = id;
    els.audio.src = e.audio_url;
    els.audio.defaultPlaybackRate = state.rate;
    els.audio.playbackRate = state.rate;
    els.player.hidden = false;
    els.nowGondi.textContent = e.gondi_text;
    els.nowMeaning.textContent = [e.telugu, e.english].filter(Boolean).join(" · ");
    if (autoplay) play();
    syncPlayingCard();
  }

  function play() {
    els.audio.playbackRate = state.rate;
    const p = els.audio.play();
    if (p && p.catch) p.catch((err) => { if (err.name !== "AbortError") announce("The recording could not be played."); });
  }

  function toggle(id) {
    if (state.current === id) {
      if (els.audio.paused || els.audio.ended) play(); else els.audio.pause();
    } else {
      load(id);
    }
  }

  function step(delta) {
    const list = state.visible.length ? state.visible : state.items;
    if (!list.length) return;
    let i = list.findIndex((e) => e.id === state.current);
    i = i === -1 ? 0 : (i + delta + list.length) % list.length;
    load(list[i].id);
    const card = document.getElementById(`x-${list[i].code}`);
    if (card) card.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  function setRate(rate) {
    state.rate = rate;
    store.set("gv:rate", rate);
    els.audio.defaultPlaybackRate = rate;
    els.audio.playbackRate = rate;
    for (const b of document.querySelectorAll(".speed button")) {
      b.setAttribute("aria-pressed", String(Number(b.dataset.rate) === rate));
    }
    announce(`Speed ${rate === 1 ? "normal" : rate + " times"}`);
  }

  // ------------------------------------------------------------------ events
  for (const ev of ["play", "pause", "ended"]) els.audio.addEventListener(ev, syncPlayingCard);
  els.audio.addEventListener("loadedmetadata", () => { els.audio.playbackRate = state.rate; });
  els.audio.addEventListener("timeupdate", () => {
    const d = els.audio.duration;
    els.bar.style.width = d ? `${(els.audio.currentTime / d) * 100}%` : "0";
  });
  els.audio.addEventListener("ended", () => { els.bar.style.width = "100%"; });
  els.audio.addEventListener("error", () => { if (els.audio.src) announce("The recording could not be loaded."); });

  els.playpause.addEventListener("click", () => { if (state.current) toggle(state.current); else step(0); });
  els.replay.addEventListener("click", () => { if (state.current) { els.audio.currentTime = 0; play(); } });
  els.prev.addEventListener("click", () => step(-1));
  els.next.addEventListener("click", () => step(1));
  for (const b of document.querySelectorAll(".speed button")) b.addEventListener("click", () => setRate(Number(b.dataset.rate)));

  let t;
  els.q.addEventListener("input", () => {
    els.clear.hidden = !els.q.value;
    clearTimeout(t);
    t = setTimeout(() => { state.query = els.q.value; applyFilters(); }, 120);
  });
  els.form.addEventListener("submit", (ev) => ev.preventDefault());
  els.clear.addEventListener("click", () => { els.q.value = ""; els.clear.hidden = true; state.query = ""; applyFilters(); els.q.focus(); });
  els.favOnly.addEventListener("change", applyFilters);

  document.addEventListener("keydown", (ev) => {
    const tag = (ev.target.tagName || "").toLowerCase();
    if (["input", "textarea", "select"].includes(tag) || ev.altKey || ev.ctrlKey || ev.metaKey) return;
    if (ev.key === " " && tag !== "button") { ev.preventDefault(); els.playpause.click(); }
    else if (ev.key === "ArrowRight") { ev.preventDefault(); step(1); }
    else if (ev.key === "ArrowLeft") { ev.preventDefault(); step(-1); }
  });

  // ------------------------------------------------------------------ init
  async function init() {
    setRate(state.rate === 0.75 ? 0.75 : 1);
    const params = new URLSearchParams(location.search);
    try {
      const [cats, data] = await Promise.all([getJSON("/api/public/categories"), getJSON("/api/public/expressions")]);
      state.categories = cats;
      state.items = data.items.map((e) => ({
        ...e,
        _haystack: norm([e.gondi_text, e.transliteration, e.telugu, e.english, e.code, e.category].join(" ")),
      }));
    } catch (err) {
      els.loading.hidden = true;
      els.empty.hidden = false;
      els.emptyTitle.textContent = "The library could not be loaded.";
      els.emptyBody.textContent = "Please check your connection and reload the page.";
      return;
    }
    els.loading.hidden = true;
    // Deep links for Moodle: ?category=Greetings  ?q=hello  #GON-001
    const catName = norm(params.get("category"));
    if (catName) {
      const c = state.categories.find((x) => norm(x.name) === catName);
      if (c) state.category = c.id;
    }
    if (params.get("q")) { els.q.value = params.get("q"); state.query = els.q.value; els.clear.hidden = false; }
    renderChips();
    applyFilters();
    if (state.items.length) {
      // Show the player straight away so speed can be chosen before the first play.
      els.player.hidden = false;
      els.nowGondi.textContent = "Choose an expression to listen";
      els.nowMeaning.textContent = "వినడానికి ఒక పదాన్ని ఎంచుకోండి";
    }
    const code = decodeURIComponent(location.hash.slice(1));
    if (code) {
      const e = state.items.find((x) => x.code === code);
      if (e) {
        load(e.id, false);
        document.getElementById(`x-${e.code}`)?.scrollIntoView({ block: "center" });
      }
    }
  }

  init();
})();
