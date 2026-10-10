/* HUD client: live channel, voice in/out, iris visual, dashboard panels. */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const clientId = Math.random().toString(36).slice(2);
  const store = {
    get(k, d) { try { const v = localStorage.getItem("hud." + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem("hud." + k, JSON.stringify(v)); } catch { /* private mode */ } },
  };

  const state = {
    name: "Panda",
    title: "sir",
    serverTts: false,
    mode: "idle",            // idle | listening | thinking | speaking
    level: 0,                 // 0..1 visual intensity
    speakEnabled: store.get("speak", true),
    wakeEnabled: store.get("wake", false),
    voiceName: store.get("voice", ""),
    rate: store.get("rate", 1.02),
    speakThisTurn: false,
    cpuHistory: [],
  };

  /* ------------------------------------------------------------ helpers */

  function escapeHtml(s) {
    return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  function inline(s) {
    return s
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>")
      .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  }

  // Small, safe markdown subset: everything is escaped before formatting.
  function renderMarkdown(src) {
    const parts = escapeHtml(src).split(/```/);
    let html = "";
    parts.forEach((part, i) => {
      if (i % 2 === 1) {
        html += "<pre><code>" + part.replace(/^[\w-]*\n/, "") + "</code></pre>";
        return;
      }
      for (const block of part.split(/\n{2,}/)) {
        const lines = block.split("\n").filter((l) => l.trim());
        if (!lines.length) continue;
        if (lines.every((l) => /^\s*[-*] /.test(l))) {
          html += "<ul>" + lines.map((l) => "<li>" + inline(l.replace(/^\s*[-*] /, "")) + "</li>").join("") + "</ul>";
        } else if (lines.every((l) => /^\s*\d+[.)] /.test(l))) {
          html += "<ol>" + lines.map((l) => "<li>" + inline(l.replace(/^\s*\d+[.)] /, "")) + "</li>").join("") + "</ol>";
        } else {
          html += "<p>" + lines.map((l) => inline(l.replace(/^#{1,6} (.*)$/, "**$1**"))).join("<br>") + "</p>";
        }
      }
    });
    return html;
  }

  // What the voice should say: no code, no markdown symbols, no URLs.
  function speakable(text) {
    return text
      .replace(/```[\s\S]*?```/g, " ")
      .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
      .replace(/https?:\/\/\S+/g, "")
      .replace(/[*_#`>|]/g, "")
      .replace(/^\s*[-•]\s+/gm, "")
      .replace(/\s+/g, " ")
      .trim();
  }

  function fmtTime(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (isNaN(d)) return iso;
    const sameDay = d.toDateString() === new Date().toDateString();
    return sameDay
      ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
      : d.toLocaleString([], { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  }

  function setMode(mode) {
    state.mode = mode;
    $("reactor-state").textContent = { idle: state.wakeEnabled ? `say "${state.name}"` : "standby", listening: "listening", thinking: "processing", speaking: "speaking" }[mode];
  }

  function toast(text, ms = 8000) {
    const el = document.createElement("div");
    el.className = "toast";
    el.textContent = text;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), ms);
  }

  /* -------------------------------------------------------------- sound */

  let audioCtx;
  function ctx() { return (audioCtx ||= new (window.AudioContext || window.webkitAudioContext)()); }

  function chime(up = true) {
    try {
      const c = ctx(), o = c.createOscillator(), g = c.createGain();
      o.type = "sine";
      o.frequency.setValueAtTime(up ? 660 : 880, c.currentTime);
      o.frequency.exponentialRampToValueAtTime(up ? 1320 : 440, c.currentTime + 0.12);
      g.gain.setValueAtTime(0.0001, c.currentTime);
      g.gain.exponentialRampToValueAtTime(0.12, c.currentTime + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, c.currentTime + 0.25);
      o.connect(g).connect(c.destination);
      o.start(); o.stop(c.currentTime + 0.26);
    } catch { /* audio blocked until first gesture */ }
  }

  /* ------------------------------------------------------- text to speech */

  const tts = {
    queue: [],
    busy: false,
    audio: null,
    voices: [],

    loadVoices() {
      if (!("speechSynthesis" in window)) return;
      this.voices = speechSynthesis.getVoices();
      const sel = $("voice-select");
      const preferred = [
        /Microsoft Ryan Online/i, /Google UK English Male/i, /Daniel/i, /Arthur/i, /Microsoft George/i,
        /Oliver/i, /en-GB.*Male/i,
      ];
      const ranked = [...this.voices].sort((a, b) => {
        const score = (v) => {
          const i = preferred.findIndex((re) => re.test(v.name + " " + v.lang));
          return (i === -1 ? 50 : i) + (v.lang.startsWith("en-GB") ? 0 : 20) + (v.lang.startsWith("en") ? 0 : 100);
        };
        return score(a) - score(b);
      });
      sel.innerHTML = "";
      if (state.serverTts) sel.add(new Option("Premium (server)", "__server__"));
      for (const v of ranked) sel.add(new Option(`${v.name} (${v.lang})`, v.name));
      if (state.voiceName && [...sel.options].some((o) => o.value === state.voiceName)) sel.value = state.voiceName;
      else { sel.selectedIndex = 0; state.voiceName = sel.value; }
    },

    say(text) {
      const clean = speakable(text);
      if (!clean || !state.speakEnabled) return;
      this.queue.push(clean);
      if (!this.busy) this.next();
    },

    next() {
      const text = this.queue.shift();
      if (text === undefined) {
        this.busy = false;
        if (state.mode === "speaking") setMode("idle");
        voice.resume();
        return;
      }
      this.busy = true;
      voice.pause();
      setMode("speaking");
      if (state.voiceName === "__server__") this.server(text);
      else this.browser(text);
    },

    browser(text) {
      if (!("speechSynthesis" in window)) { this.next(); return; }
      const u = new SpeechSynthesisUtterance(text);
      const v = this.voices.find((x) => x.name === state.voiceName);
      if (v) { u.voice = v; u.lang = v.lang; }
      u.rate = state.rate;
      u.pitch = 0.92;
      u.onend = u.onerror = () => this.next();
      speechSynthesis.speak(u);
    },

    async server(text) {
      try {
        const res = await fetch("/api/tts", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ text }) });
        if (!res.ok) throw new Error("tts " + res.status);
        const url = URL.createObjectURL(await res.blob());
        this.audio = new Audio(url);
        this.audio.playbackRate = state.rate;
        this.audio.onended = this.audio.onerror = () => { URL.revokeObjectURL(url); this.next(); };
        await this.audio.play();
      } catch {
        this.browser(text);
      }
    },

    stop() {
      this.queue = [];
      if ("speechSynthesis" in window) speechSynthesis.cancel();
      if (this.audio) { this.audio.pause(); this.audio = null; }
      this.busy = false;
      if (state.mode === "speaking") setMode("idle");
      voice.resume();
    },
  };

  // Splits the streaming reply into sentences so speech starts before the reply ends.
  const sentenceBuffer = {
    buf: "",
    inCode: false,
    push(delta) {
      this.buf += delta;
      for (;;) {
        if (this.inCode) {
          const end = this.buf.indexOf("```");
          if (end === -1) return;
          this.buf = this.buf.slice(end + 3);
          this.inCode = false;
          continue;
        }
        const fence = this.buf.indexOf("```");
        const m = /([.!?…:;])(\s+)|\n+/.exec(this.buf);
        if (fence !== -1 && (!m || fence < m.index)) {
          this.emit(this.buf.slice(0, fence));
          this.buf = this.buf.slice(fence + 3);
          this.inCode = true;
          continue;
        }
        if (!m) return;
        const cut = m.index + m[0].length;
        const sentence = this.buf.slice(0, cut);
        if (sentence.trim().length < 12 && m[0][0] !== "\n" && this.buf.length < 400) {
          // very short fragment ("Mr.", "3.") - wait for more text unless a newline
          const next = /([.!?…])(\s+)|\n+/g;
          next.lastIndex = cut;
          if (!next.exec(this.buf)) return;
        }
        this.emit(sentence);
        this.buf = this.buf.slice(cut);
      }
    },
    flush() {
      if (!this.inCode) this.emit(this.buf);
      this.buf = "";
      this.inCode = false;
    },
    emit(text) {
      if (state.speakThisTurn && text.trim()) tts.say(text);
    },
  };

  /* ------------------------------------------------------- speech to text */

  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const voice = {
    rec: null,
    active: false,        // recognition currently running
    pushToTalk: false,
    paused: false,
    awaitingCommand: 0,   // timestamp until which the wake word is "armed"

    supported() { return !!SR; },

    ensure() {
      if (this.rec || !SR) return;
      const rec = new SR();
      rec.lang = navigator.language || "en-GB";
      rec.interimResults = true;
      rec.continuous = true;
      rec.onresult = (e) => this.onResult(e);
      rec.onend = () => {
        this.active = false;
        if (state.mode === "listening") setMode("idle");
        if (this.pushToTalk) { this.pushToTalk = false; $("btn-mic").classList.remove("on"); }
        if (state.wakeEnabled && !this.paused) setTimeout(() => this.start(), 300);
      };
      rec.onerror = (e) => {
        if (e.error === "not-allowed" || e.error === "service-not-allowed") {
          state.wakeEnabled = false; store.set("wake", false); renderToggles();
          toast("Microphone access was denied.");
        }
      };
      this.rec = rec;
    },

    start() {
      this.ensure();
      if (!this.rec || this.active || this.paused) return;
      try { this.rec.start(); this.active = true; } catch { /* already started */ }
      if (this.pushToTalk) setMode("listening");
    },

    stop() {
      if (this.rec && this.active) { try { this.rec.stop(); } catch { /* ignore */ } }
    },

    pause() { this.paused = true; if (this.rec && this.active) { try { this.rec.abort(); } catch { /* ignore */ } } },
    resume() {
      if (!this.paused) return;
      this.paused = false;
      if (state.wakeEnabled) setTimeout(() => this.start(), 250);
    },

    talk() {
      if (!SR) { toast("Voice input needs Chrome, Edge or Safari."); return; }
      tts.stop();
      chime(true);
      this.pushToTalk = true;
      $("btn-mic").classList.add("on");
      if (this.active) setMode("listening"); else this.start();
      mic.open();
    },

    onResult(e) {
      let interim = "", final = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const r = e.results[i];
        if (r.isFinal) final += r[0].transcript; else interim += r[0].transcript;
      }
      const wakeRe = new RegExp("\\b(?:hey |ok |okay )?" + state.name.replace(/[^\w]/g, "") + "\\b[,.!?]?\\s*", "i");
      const armed = this.pushToTalk || Date.now() < this.awaitingCommand;

      if (interim) {
        if (armed || wakeRe.test(interim)) {
          setMode("listening");
          showCaption(interim.replace(wakeRe, ""), true);
        }
      }
      if (!final) return;
      final = final.trim();

      if (armed) {
        this.awaitingCommand = 0;
        if (this.pushToTalk) { this.pushToTalk = false; $("btn-mic").classList.remove("on"); if (!state.wakeEnabled) this.stop(); }
        send(final.replace(wakeRe, ""));
        return;
      }
      const m = wakeRe.exec(final);
      if (!m) return;
      const rest = final.slice(m.index + m[0].length).trim();
      if (rest.length > 2) {
        send(rest);
      } else {
        chime(true);
        this.awaitingCommand = Date.now() + 8000;
        setMode("listening");
        showCaption(`Yes, ${state.title}?`, false);
      }
    },
  };

  // Microphone level for the iris (optional; purely visual).
  const mic = {
    analyser: null,
    data: null,
    async open() {
      if (this.analyser || !navigator.mediaDevices) return;
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
        const src = ctx().createMediaStreamSource(stream);
        this.analyser = ctx().createAnalyser();
        this.analyser.fftSize = 256;
        this.data = new Uint8Array(this.analyser.frequencyBinCount);
        src.connect(this.analyser);
      } catch { /* visual only */ }
    },
    level() {
      if (!this.analyser) return 0;
      this.analyser.getByteTimeDomainData(this.data);
      let sum = 0;
      for (const v of this.data) sum += ((v - 128) / 128) ** 2;
      return Math.min(1, Math.sqrt(sum / this.data.length) * 4);
    },
  };

  /* ------------------------------------------------------------- iris */

  // The presence: an eye. Iris fibres breathe with the voice, the pupil dilates
  // when listening, caustic ripples drift outward, a scan line sweeps the lens.
  function fitCanvas(canvas, onSize) {
    const g = canvas.getContext("2d");
    const resize = () => {
      const dpr = window.devicePixelRatio || 1;
      const r = canvas.getBoundingClientRect();
      canvas.width = r.width * dpr; canvas.height = r.height * dpr;
      g.setTransform(dpr, 0, 0, dpr, 0, 0);
      onSize(r.width, r.height);
    };
    new ResizeObserver(resize).observe(canvas);
    return g;
  }

  (() => {
    let w = 0, h = 0, t = 0;
    const g = fitCanvas($("reactor-canvas"), (W, H) => { w = W; h = H; });
    const colors = {
      idle: [255, 154, 60], listening: [255, 79, 184], thinking: [95, 227, 214], speaking: [255, 196, 120],
    };
    let cur = [...colors.idle];
    let pupil = 0.32;
    // fixed per-fibre randomness so the iris has texture, not noise
    const fibres = Array.from({ length: 220 }, () => ({ len: 0.55 + Math.random() * 0.45, phase: Math.random() * 6.28, w: 0.4 + Math.random() * 1.1 }));

    function frame() {
      requestAnimationFrame(frame);
      if (w < 40 || h < 40) return;
      t += 1 / 60;
      const target = colors[state.mode];
      cur = cur.map((c, i) => c + (target[i] - c) * 0.06);
      const rgb = (a) => `rgba(${cur[0] | 0},${cur[1] | 0},${cur[2] | 0},${a})`;

      let lvl;
      if (state.mode === "listening") lvl = 0.15 + mic.level();
      else if (state.mode === "speaking") lvl = 0.3 + 0.3 * Math.abs(Math.sin(t * 8.5)) * (0.6 + 0.4 * Math.sin(t * 2.1));
      else if (state.mode === "thinking") lvl = 0.22 + 0.08 * Math.sin(t * 5);
      else lvl = 0.05 + 0.03 * Math.sin(t * 1.2);
      state.level += (lvl - state.level) * 0.18;

      const pupilTarget = { idle: 0.3, listening: 0.42, thinking: 0.24, speaking: 0.34 }[state.mode] + state.level * 0.05;
      pupil += (pupilTarget - pupil) * 0.05;

      const cx = w / 2, cy = h * 0.46, R = Math.min(w, h) * 0.42;
      g.clearRect(0, 0, w, h);

      // haze bloom behind the eye
      const bloom = g.createRadialGradient(cx, cy, R * 0.2, cx, cy, R * 1.25);
      bloom.addColorStop(0, rgb(0.22 + state.level * 0.25));
      bloom.addColorStop(0.5, rgb(0.06));
      bloom.addColorStop(1, rgb(0));
      g.fillStyle = bloom;
      g.fillRect(0, 0, w, h);

      // caustic ripples drifting outward (light through water)
      for (let k = 0; k < 4; k++) {
        const p = ((t * 0.12 + k / 4) % 1);
        g.strokeStyle = rgb(0.18 * (1 - p));
        g.lineWidth = 1;
        g.beginPath();
        for (let a = 0; a <= Math.PI * 2 + 0.01; a += Math.PI / 90) {
          const wob = 1 + 0.03 * Math.sin(a * 5 + t * 1.3 + k) + 0.02 * Math.sin(a * 9 - t);
          const r = R * (0.95 + p * 0.5) * wob;
          const x = cx + Math.cos(a) * r, y = cy + Math.sin(a) * r;
          a === 0 ? g.moveTo(x, y) : g.lineTo(x, y);
        }
        g.stroke();
      }

      // iris fibres
      const inner = R * pupil, rot = t * (state.mode === "thinking" ? 0.35 : 0.04);
      for (let i = 0; i < fibres.length; i++) {
        const f = fibres[i];
        const a = (i / fibres.length) * Math.PI * 2 + rot;
        const pulse = 0.85 + 0.15 * Math.sin(t * 3 + f.phase) + state.level * 0.35 * Math.sin(t * 9 + f.phase);
        const outer = inner + (R - inner) * f.len * Math.min(1.08, pulse);
        const grad = g.createLinearGradient(cx + Math.cos(a) * inner, cy + Math.sin(a) * inner, cx + Math.cos(a) * outer, cy + Math.sin(a) * outer);
        grad.addColorStop(0, rgb(0.85));
        grad.addColorStop(1, rgb(0));
        g.strokeStyle = grad;
        g.lineWidth = f.w;
        g.beginPath();
        g.moveTo(cx + Math.cos(a) * inner, cy + Math.sin(a) * inner);
        g.lineTo(cx + Math.cos(a) * outer, cy + Math.sin(a) * outer);
        g.stroke();
      }

      // limbal ring and outer hairlines
      g.strokeStyle = rgb(0.55);
      g.lineWidth = 1.5;
      g.beginPath(); g.arc(cx, cy, R, 0, Math.PI * 2); g.stroke();
      g.strokeStyle = rgb(0.25);
      g.lineWidth = 1;
      for (const [r, a0, a1] of [[1.12, -0.9, 0.9], [1.12, Math.PI - 0.9, Math.PI + 0.9], [1.2, -0.25, 0.25], [1.2, Math.PI - 0.25, Math.PI + 0.25]]) {
        g.beginPath(); g.arc(cx, cy, R * r, a0, a1); g.stroke();
      }

      // pupil with a warm rim and a catch-light
      const pg = g.createRadialGradient(cx, cy, inner * 0.2, cx, cy, inner * 1.05);
      pg.addColorStop(0, "rgba(5,3,2,1)");
      pg.addColorStop(0.85, "rgba(10,5,3,0.98)");
      pg.addColorStop(1, rgb(0.6));
      g.fillStyle = pg;
      g.beginPath(); g.arc(cx, cy, inner, 0, Math.PI * 2); g.fill();
      g.fillStyle = "rgba(255,240,220,0.55)";
      g.beginPath(); g.ellipse(cx - inner * 0.35, cy - inner * 0.4, inner * 0.14, inner * 0.08, -0.6, 0, Math.PI * 2); g.fill();

      // horizontal scan line sweeping the lens
      const sy = cy - R + ((t * 0.25) % 1) * R * 2;
      const half = Math.sqrt(Math.max(0, R * R - (sy - cy) ** 2));
      g.strokeStyle = rgb(0.35);
      g.lineWidth = 1;
      g.beginPath(); g.moveTo(cx - half - 18, sy); g.lineTo(cx + half + 18, sy); g.stroke();
    }
    requestAnimationFrame(frame);
  })();

  /* ------------------------------------------------------- atmosphere */

  // Rain streaks and drifting dust over the amber haze. Cheap enough for phones.
  (() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    let w = 0, h = 0;
    const g = fitCanvas($("atmosphere"), (W, H) => { w = W; h = H; });
    const rand = (a, b) => a + Math.random() * (b - a);
    const drops = Array.from({ length: 140 }, () => ({ x: rand(0, 1), y: rand(0, 1), v: rand(0.9, 1.6), l: rand(10, 28), a: rand(0.05, 0.16) }));
    const dust = Array.from({ length: 45 }, () => ({ x: rand(0, 1), y: rand(0, 1), r: rand(0.6, 2.2), vx: rand(-0.02, 0.02), vy: rand(-0.012, 0.004), p: rand(0, 6.28) }));
    let last = performance.now();

    function frame(now) {
      requestAnimationFrame(frame);
      const dt = Math.min(0.05, (now - last) / 1000);
      last = now;
      if (!w || document.hidden) return;
      g.clearRect(0, 0, w, h);

      g.lineWidth = 1;
      for (const d of drops) {
        d.y += d.v * dt * 0.9;
        d.x += d.v * dt * 0.08;
        if (d.y > 1.05) { d.y = -0.05; d.x = rand(-0.1, 1); }
        const x = d.x * w, y = d.y * h;
        g.strokeStyle = `rgba(232,201,160,${d.a})`;
        g.beginPath(); g.moveTo(x, y); g.lineTo(x + d.l * 0.09, y + d.l); g.stroke();
      }
      for (const p of dust) {
        p.x += p.vx * dt; p.y += p.vy * dt; p.p += dt;
        if (p.x < -0.02) p.x = 1.02; if (p.x > 1.02) p.x = -0.02;
        if (p.y < -0.02) p.y = 1.02; if (p.y > 1.02) p.y = -0.02;
        g.fillStyle = `rgba(255,170,90,${0.12 + 0.12 * Math.sin(p.p)})`;
        g.beginPath(); g.arc(p.x * w, p.y * h, p.r, 0, Math.PI * 2); g.fill();
      }
    }
    requestAnimationFrame(frame);
  })();

  /* --------------------------------------------------------- transcript */

  let liveMsg = null;
  let liveText = "";

  function addMessage(role, text, opts = {}) {
    const el = document.createElement("div");
    el.className = "msg " + role + (opts.error ? " error" : "");
    el.innerHTML = `<div class="who">${role === "user" ? escapeHtml(state.owner || "you") : escapeHtml(state.name)}${opts.at ? " · " + fmtTime(opts.at) : ""}</div><div class="body"></div>`;
    el.querySelector(".body").innerHTML = renderMarkdown(text);
    $("transcript").appendChild(el);
    scrollDown();
    return el;
  }

  function scrollDown() { const t = $("transcript"); t.scrollTop = t.scrollHeight; }

  function showCaption(text, isUser) {
    const c = $("caption");
    c.textContent = text.length > 220 ? "…" + text.slice(-220) : text;
    c.classList.toggle("user", !!isUser);
  }

  /* ---------------------------------------------------------- websocket */

  let ws;
  let backoff = 500;

  function connect() {
    ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws");
    ws.onopen = () => {
      backoff = 500;
      $("link-dot").classList.add("on");
      $("link-text").textContent = "uplink secure";
      loadDashboard();
    };
    ws.onclose = (e) => {
      $("link-dot").classList.remove("on");
      $("link-text").textContent = "offline";
      if (e.code === 4401) { location.href = "/login"; return; }
      setTimeout(connect, backoff);
      backoff = Math.min(backoff * 2, 15000);
    };
    ws.onmessage = (e) => handle(JSON.parse(e.data));
  }

  function send(text) {
    text = text.trim();
    if (!text) return;
    if (!ws || ws.readyState !== WebSocket.OPEN) { toast("No uplink to the server. Reconnecting…"); return; }
    tts.stop();
    ws.send(JSON.stringify({ type: "say", text, client: clientId }));
  }

  function handle(ev) {
    switch (ev.type) {
      case "user":
        state.speakThisTurn = ev.client === clientId;
        addMessage("user", ev.text);
        showCaption(ev.text, true);
        setMode("thinking");
        break;
      case "start":
        liveText = "";
        liveMsg = addMessage("assistant", "");
        break;
      case "delta":
        liveText += ev.text;
        if (liveMsg) liveMsg.querySelector(".body").innerHTML = renderMarkdown(liveText);
        showCaption(speakable(liveText).split(/(?<=[.!?])\s/).slice(-2).join(" "), false);
        sentenceBuffer.push(ev.text);
        scrollDown();
        break;
      case "tool":
        if (liveMsg) {
          const tag = document.createElement("span");
          tag.className = "tool";
          tag.textContent = ev.name.replace(/_/g, " ").toUpperCase();
          liveMsg.querySelector(".who").appendChild(document.createTextNode(" ")); liveMsg.querySelector(".who").appendChild(tag);
        }
        break;
      case "done":
        sentenceBuffer.flush();
        if (!tts.busy && state.mode !== "listening") setMode("idle");
        liveMsg = null;
        if (ev.refresh) loadDashboard();
        break;
      case "error":
        sentenceBuffer.flush();
        if (liveMsg) { liveMsg.remove(); liveMsg = null; }
        addMessage("assistant", ev.text, { error: true });
        if (state.speakThisTurn) tts.say(ev.text);
        if (!tts.busy) setMode("idle");
        break;
      case "reminder":
        chime(false);
        toast("ALERT · " + ev.text, 20000);
        state.speakThisTurn = true;
        tts.say(`Pardon the interruption, ${state.title}. A reminder: ${ev.text}`);
        if ("Notification" in window && Notification.permission === "granted" && document.hidden) {
          new Notification(state.name + " reminder", { body: ev.text });
        }
        loadDashboard();
        break;
      case "system":
        renderSystem(ev.data);
        break;
      case "refresh":
        loadDashboard();
        break;
      case "reset":
        $("transcript").innerHTML = "";
        showCaption("New session started. Memory intact.", false);
        break;
    }
  }

  /* ---------------------------------------------------------- dashboard */

  async function loadDashboard() {
    const res = await fetch("/api/dashboard");
    if (res.status === 401) { location.href = "/login"; return; }
    const d = await res.json();
    const firstLoad = !state.loaded;
    state.loaded = true;
    state.name = d.assistant;
    state.owner = d.owner;
    state.title = d.title;
    if (state.serverTts !== d.server_tts) { state.serverTts = d.server_tts; tts.loadVoices(); }
    document.title = d.assistant + " Interface";
    $("brand").textContent = d.assistant.toUpperCase();
    $("reactor-name").textContent = d.assistant.toUpperCase();
    setMode(state.mode);

    renderSystem(d.system);
    renderWeather(d.weather);
    renderList("tasks", d.tasks, (t) => ({
      cls: t.priority === "high" ? "high" : "",
      html: `${escapeHtml(t.title)}<span class="meta">#${t.id}${t.due ? " · due " + escapeHtml(fmtTime(t.due)) : ""}${t.priority !== "normal" ? " · " + t.priority : ""}</span>`,
      action: ["done", () => post(`/api/tasks/${t.id}/complete`)],
    }), "No open directives.");
    $("task-count").textContent = d.tasks.length ? d.tasks.length + " open" : "";
    renderList("reminders", d.reminders, (r) => ({
      html: `${escapeHtml(r.text)}<span class="meta">${escapeHtml(fmtTime(r.due_at))}</span>`,
      action: ["cancel", () => post(`/api/reminders/${r.id}/cancel`)],
    }), "Nothing scheduled.");
    renderList("memories", d.memories, (m) => ({
      html: `${escapeHtml(m.content)}<span class="meta">${escapeHtml(m.category)} · importance ${m.importance}</span>`,
      action: ["forget", () => fetch(`/api/memories/${m.id}`, { method: "DELETE" })],
    }), "Memory core empty. Tell me about yourself.");
    $("mem-count").textContent = d.memory_count + " stored";
    renderList("notes", d.notes, (n) => ({
      html: `${escapeHtml(n.title)}<span class="meta">${escapeHtml(fmtTime(n.created_at))}</span>`,
    }), "No notes yet.");

    if (firstLoad) {
      $("transcript").innerHTML = "";
      for (const m of d.transcript) addMessage(m.role, m.text, { at: m.at, error: m.error });
    }
  }

  function post(url) { return fetch(url, { method: "POST" }); }

  function renderList(id, items, fn, emptyText) {
    const ul = $(id);
    ul.innerHTML = "";
    if (!items.length) { ul.innerHTML = `<li class="empty">${emptyText}</li>`; return; }
    for (const item of items) {
      const spec = fn(item);
      const li = document.createElement("li");
      if (spec.cls) li.className = spec.cls;
      li.innerHTML = `<div>${spec.html}</div>`;
      if (spec.action) {
        const b = document.createElement("button");
        b.textContent = spec.action[0];
        b.onclick = spec.action[1];
        li.appendChild(b);
      }
      ul.appendChild(li);
    }
  }

  function renderSystem(s) {
    if (!s) return;
    $("sys-host").textContent = s.host;
    document.querySelectorAll(".gauge").forEach((el) => {
      const v = s[el.dataset.key] ?? 0;
      const fill = el.querySelector(".fill");
      fill.style.width = Math.min(100, v) + "%";
      fill.classList.toggle("warn", v >= 75 && v < 90);
      fill.classList.toggle("crit", v >= 90);
      el.querySelector(".val").textContent = Math.round(v) + "%";
    });
    state.cpuHistory.push(s.cpu_percent);
    if (state.cpuHistory.length > 60) state.cpuHistory.shift();
    const pts = state.cpuHistory.map((v, i) => [(i / 59) * 100, 40 - (v / 100) * 38]);
    const line = pts.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" ");
    $("cpu-spark").querySelector(".line").setAttribute("d", line);
    if (pts.length) $("cpu-spark").querySelector(".area").setAttribute("d", line + ` L${pts[pts.length - 1][0].toFixed(1)} 40 L0 40 Z`);
    $("sys-kv").innerHTML = [
      ["Memory", `${s.mem_used_gb} / ${s.mem_total_gb} GB`],
      ["Disk", `${s.disk_used_gb} / ${s.disk_total_gb} GB`],
      ["Load", s.load.join("  ")],
      ["Net ↓ / ↑", `${s.net_rx_kbps} / ${s.net_tx_kbps} KB/s`],
      ["Uptime", `${s.uptime_hours} h`],
      ["Processes", s.processes],
    ].map(([k, v]) => `<dt>${k}</dt><dd>${escapeHtml(String(v))}</dd>`).join("");
  }

  function renderWeather(wx) {
    if (!wx || wx.error) return;
    $("wx-place").textContent = wx.location;
    $("wx-body").className = "";
    $("wx-body").innerHTML =
      `<div class="weather-now"><span class="temp">${Math.round(wx.now.temp_c)}°</span><span>${escapeHtml(wx.now.conditions)}<br><small class="empty">feels ${Math.round(wx.now.feels_like_c)}° · wind ${Math.round(wx.now.wind_kmh)} km/h</small></span></div>` +
      `<div class="weather-days">${wx.forecast.map((d) => `<div><b>${new Date(d.date).toLocaleDateString([], { weekday: "short" })}</b>${Math.round(d.min_c)}° / ${Math.round(d.max_c)}°<br>${d.rain_chance ?? 0}% rain</div>`).join("")}</div>`;
  }

  /* ----------------------------------------------------------- controls */

  function renderToggles() {
    $("btn-wake").textContent = state.wakeEnabled ? "On" : "Off";
    $("btn-wake").classList.toggle("on", state.wakeEnabled);
    $("btn-speak").textContent = state.speakEnabled ? "On" : "Off";
    $("btn-speak").classList.toggle("on", state.speakEnabled);
    setMode(state.mode);
  }

  $("cmd-form").addEventListener("submit", (e) => {
    e.preventDefault();
    send($("cmd").value);
    $("cmd").value = "";
  });

  $("btn-mic").addEventListener("click", () => voice.talk());
  $("reactor").addEventListener("click", () => (tts.busy ? tts.stop() : voice.talk()));

  $("btn-wake").addEventListener("click", () => {
    if (!voice.supported()) { toast("Wake word needs Chrome or Edge."); return; }
    state.wakeEnabled = !state.wakeEnabled;
    store.set("wake", state.wakeEnabled);
    renderToggles();
    if (state.wakeEnabled) { voice.start(); mic.open(); } else voice.stop();
    if ("Notification" in window && Notification.permission === "default") Notification.requestPermission();
  });
  $("btn-speak").addEventListener("click", () => {
    state.speakEnabled = !state.speakEnabled;
    store.set("speak", state.speakEnabled);
    if (!state.speakEnabled) tts.stop();
    renderToggles();
  });
  $("voice-select").addEventListener("change", (e) => { state.voiceName = e.target.value; store.set("voice", state.voiceName); tts.say(`Voice calibrated, ${state.title}.`); });
  $("voice-rate").value = state.rate;
  $("voice-rate").addEventListener("change", (e) => { state.rate = parseFloat(e.target.value); store.set("rate", state.rate); });

  $("btn-new").addEventListener("click", () => ws && ws.send(JSON.stringify({ type: "reset" })));
  $("btn-briefing").addEventListener("click", () => send("Give me my briefing: time, weather, what's on my task list and any reminders, plus anything I should know."));
  $("btn-calibrate").addEventListener("click", () => send(
    "Let's calibrate. Interview me so you can get to know me: who I am, what I do, the projects and people that matter, " +
    "my daily routine, and how I like you to work. One short question at a time, and save what you learn as you go."));
  $("btn-logout").addEventListener("click", async () => { await post("/api/logout"); location.href = "/login"; });

  // Hold Space to talk (when not typing); Escape silences speech.
  let spaceDown = false;
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { tts.stop(); return; }
    if (e.code !== "Space" || e.repeat || document.activeElement === $("cmd")) return;
    e.preventDefault();
    spaceDown = true;
    voice.talk();
  });
  document.addEventListener("keyup", (e) => {
    if (e.code === "Space" && spaceDown) { spaceDown = false; if (voice.pushToTalk) voice.stop(); }
  });

  setInterval(() => {
    const d = new Date();
    $("clock").textContent = d.toLocaleTimeString([], { hour12: false }) + "  " + d.toLocaleDateString([], { weekday: "short", day: "2-digit", month: "short" }).toUpperCase();
  }, 1000);

  if ("speechSynthesis" in window) {
    tts.loadVoices();
    speechSynthesis.onvoiceschanged = () => tts.loadVoices();
  }

  /* --------------------------------------------------------------- boot */

  (async function boot() {
    const lines = [
      "PANDA // PERSONAL INTELLIGENCE",
      "",
      "MEMORY ARCHIVE ............ MOUNTED",
      "VOICE SYNTHESIS ........... CALIBRATED",
      "BASELINE .................. WITHIN RANGE",
      "UPLINK .................... SECURE",
      "",
      "ONLINE.",
    ];
    const log = $("boot-log");
    for (const l of lines) {
      log.textContent += l + "\n";
      await new Promise((r) => setTimeout(r, 160));
    }
    $("boot").classList.add("done");
    setTimeout(() => $("boot").remove(), 900);
  })();

  renderToggles();
  connect();
  if (state.wakeEnabled && voice.supported()) {
    // Browsers require a user gesture before the mic starts; arm on first interaction.
    const arm = () => { voice.start(); mic.open(); document.removeEventListener("pointerdown", arm); };
    document.addEventListener("pointerdown", arm);
    showCaption("Wake word armed. Tap anywhere to activate the microphone.", false);
  }
})();
