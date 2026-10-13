/* J.A.R.V.I.S. HUD — client logic */
"use strict";

const $ = (s) => document.querySelector(s);

const state = { online: false, ws: null, recognition: null, listening: false };

/* ---------- toast ---------- */
let toastTimer = null;
function toast(msg, warn = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "show" + (warn ? " warn" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.className = ""), 3400);
}

/* ---------- speech synthesis (browser voice) ---------- */
let voices = [];
function pickVoice() {
  voices = speechSynthesis.getVoices();
  // prefer a British male voice for the JARVIS feel
  return (
    voices.find(v => /en-GB/i.test(v.lang) && /male|daniel|george|ryan/i.test(v.name)) ||
    voices.find(v => /en-GB/i.test(v.lang)) ||
    voices.find(v => /en/i.test(v.lang)) ||
    voices[0]
  );
}
function speak(text) {
  try {
    speechSynthesis.cancel();
    if (!text) return;
    const u = new SpeechSynthesisUtterance(text);
    const v = pickVoice();
    if (v) u.voice = v;
    u.rate = 1.04; u.pitch = 0.72; u.volume = 1.0;
    speechSynthesis.speak(u);
  } catch (e) { /* fallback: backend TTS */ 
    fetch("/api/tts", { method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({ text }) }).catch(()=>{});
  }
}
if ("speechSynthesis" in window) speechSynthesis.onvoiceschanged = () => pickVoice();

/* ---------- command execution ---------- */
async function runCommand(text, { speakIt = true, fromVoice = false } = {}) {
  if (!text) return;
  addHistory(text, "…");
  try {
    const r = await fetch("/api/command", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    const data = await r.json();
    addHistory(text, data.reply || "");
    if (data.needs_confirm) toast(data.reply, true);
    if (speakIt) speak(data.reply || "");
    refreshState();
    return data;
  } catch (e) {
    addHistory(text, "connection error — is the server running?");
    toast("Command failed — server unreachable", true);
  }
}

/* ---------- history ---------- */
function addHistory(user, jarvis) {
  const h = $("#history");
  const line = document.createElement("div");
  line.className = "line";
  const u = document.createElement("span");
  u.className = "u"; u.textContent = "▸ " + user;
  const j = document.createElement("span");
  j.className = "j"; j.textContent = " " + jarvis;
  line.appendChild(u); line.appendChild(j);
  h.prepend(line);
  while (h.children.length > 40) h.removeChild(h.lastChild);
}

/* ---------- power toggle ---------- */
async function setPower(on, { silent = false } = {}) {
  const r = await fetch("/api/power", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ online: on }),
  });
  const data = await r.json();
  applyPowerState(on);
  addHistory(on ? "power on" : "power off", data.reply);
  if (!silent) speak(data.reply);
  refreshState();
}
function applyPowerState(on) {
  state.online = on;
  const core = $("#core");
  core.classList.toggle("off", !on);
  $("#core-center").textContent = on ? "ON" : "OFF";
  $("#core-state").textContent = on ? "SYSTEM ONLINE" : "POWERED DOWN";
  $("#core-state").classList.toggle("off", !on);
  $("#status-dot").className = "dot " + (on ? "on" : "off");
  $("#status-text").textContent = on ? "SYSTEM ONLINE" : "OFFLINE";
}

/* ---------- core click ---------- */
$("#core").addEventListener("click", () => {
  if (state.online) setPower(false);
  else setPower(true);
});

/* ---------- command form ---------- */
$("#cmd-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = $("#cmd-input");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  await runCommand(text);
});

/* ---------- chips ---------- */
document.querySelectorAll(".chip").forEach((c) =>
  c.addEventListener("click", () => runCommand(c.dataset.cmd))
);

/* ---------- app launcher ---------- */
$("#app-launch").addEventListener("click", async () => {
  const name = $("#app-input").value.trim();
  if (!name) return;
  $("#app-input").value = "";
  await runCommand("open " + name);
});
$("#app-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); $("#app-launch").click(); }
});

/* ---------- always-on offline ears (server vosk) ---------- */
function renderEars(on) {
  $("#ears-chip").textContent = on ? "👂 EARS OPEN" : "🔇 EARS CLOSED";
  const b = $("#ears-btn");
  b.classList.toggle("ears-on", on);
  b.title = on ? "Always-on ears OPEN — say 'JARVIS <command>' anywhere. Click to close."
               : "Ears closed. Click to open always-on listening.";
}
$("#ears-btn").addEventListener("click", async () => {
  const on = !$("#ears-chip").textContent.includes("OPEN");
  const r = await fetch("/api/ears", { method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled: on }) });
  const d = await r.json();
  toast(d.reply);
  speak(d.reply);
  refreshState();
});

/* ---------- voice recognition (browser) ---------- */
function setupRecognition() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) {
    $("#mic-btn").style.opacity = 0.35;
    $("#mic-btn").title = "Speech recognition not available in this browser";
    return;
  }
  const rec = new SR();
  rec.lang = "en-IN"; // adapt: en-US / en-GB
  rec.interimResults = false;
  rec.maxAlternatives = 1;
  rec.continuous = false;

  rec.onstart = () => {
    state.listening = true;
    $("#mic-btn").classList.add("listening");
    toast("Listening…");
  };
  rec.onend = () => {
    state.listening = false;
    $("#mic-btn").classList.remove("listening");
  };
  rec.onerror = (e) => {
    state.listening = false;
    $("#mic-btn").classList.remove("listening");
    if (e.error === "not-allowed") toast("Microphone permission denied", true);
    else if (e.error === "no-speech") toast("No speech detected");
    else toast("Mic error: " + e.error, true);
  };
  rec.onresult = (e) => {
    const said = e.results[0][0].transcript.trim();
    addHistory("🎙 " + said, "…");
    runCommand(said, { fromVoice: true });
  };
  state.recognition = rec;

  $("#mic-btn").addEventListener("click", () => {
    if (state.listening) { rec.stop(); return; }
    try { speechSynthesis.cancel(); rec.start(); } catch (e) {}
  });
}

/* ---------- wake word (continuous mode) ---------- */
let wakeActive = false;
function startWakeListening() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) return;
  const rec = new SR();
  rec.lang = "en-IN";
  rec.continuous = true;
  rec.interimResults = true;
  rec.onresult = (e) => {
    for (let i = e.resultIndex; i < e.results.length; i++) {
      const t = e.results[i][0].transcript.toLowerCase();
      if (t.includes("jarvis") && !wakeActive) {
        // strip the wake word and execute the rest if present
        let cmd = t.split("jarvis").pop().trim();
        if (cmd && cmd.length > 2) runCommand(cmd, { fromVoice: true });
        else toast('Wake word heard — awaiting command…');
        break;
      }
    }
  };
  rec.onend = () => { if (wakeActive) setTimeout(() => rec.start(), 400); };
  rec.onerror = (e) => { if (e.error === "not-allowed") { wakeActive = false; } };
  wakeActive = true;
  rec.start();
  state.wakeRec = rec;
}

/* ---------- websocket telemetry ---------- */
function connectWS() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "telemetry") renderTelemetry(msg);
    else if (msg.type === "power") applyPowerState(msg.online);
    else if (msg.type === "command") {
      addHistory(msg.text, msg.reply || "");
      if (msg.reply) speak(msg.reply);
    }
    else if (msg.type === "ears") {
      renderEars(msg.enabled);
    }
    else if (msg.type === "proactive") {
      addHistory("(" + (msg.kind || "proactive") + ")", msg.text);
      const t = $("#toast");
      t.textContent = msg.text;
      t.className = "show " + (msg.kind === "reminder" ? "reminder" : "proactive");
      clearTimeout(toastTimer);
      toastTimer = setTimeout(() => (t.className = ""), 5000);
      speak(msg.text);
    }
  };
  ws.onclose = () => setTimeout(connectWS, 2000);
  ws.onopen = () => refreshState();
  state.ws = ws;
}

function renderTelemetry(t) {
  const setBar = (barId, valId, pct) => {
    const bar = $(barId);
    bar.querySelector("div").style.width = Math.min(100, pct) + "%";
    bar.className = "bar" + (pct > 90 ? " crit" : pct > 70 ? " warn" : "");
    $(valId).textContent = Math.round(pct) + "%";
  };
  setBar("#cpu-bar", "#cpu-val", t.cpu || 0);
  setBar("#ram-bar", "#ram-val", t.ram ? t.ram.percent : 0);
  setBar("#vol-bar", "#vol-val", t.volume || 0);
  if (typeof t.brightness === "number" && t.brightness >= 0)
    setBar("#bri-bar", "#bri-val", t.brightness);
  else { $("#bri-val").textContent = "—"; }

  if (t.battery) {
    $("#bat-pct").textContent = Math.round(t.battery.percent) + "%";
    $("#bat-plug").textContent = t.battery.plugged ? "AC POWER" : "ON BATTERY";
    $("#bat-plug").style.color = t.battery.plugged ? "var(--green)" : "var(--gold)";
    if (!t.battery.plugged) {
      const secsLeft = (t.battery.percent / 100) * 4 * 3600; // rough estimate
      const h = Math.floor(secsLeft / 3600), m = Math.floor((secsLeft % 3600) / 60);
      $("#bat-note").textContent = `est. ${h}h ${m}m`;
    } else $("#bat-note").textContent = "charging";
  } else {
    $("#bat-pct").textContent = "AC";
    $("#bat-plug").textContent = "MAINS POWER";
    $("#bat-note").textContent = "";
  }

  if (t.disks) {
    const d = $("#disks");
    // preserve the network block (second #disks is separate element)
    d.querySelectorAll(".d[data-mount]").forEach((n) => n.remove());
    t.disks.forEach((disk) => {
      const row = document.createElement("div");
      row.className = "d"; row.dataset.mount = disk.mount;
      row.innerHTML = `<span>${disk.mount}</span><b>${disk.free_gb} GB free</b>`;
      d.prepend(row);
    });
  }

  $("#net-up").textContent = (t.net ? t.net.sent_mb : 0) + " MB";
  $("#net-down").textContent = (t.net ? t.net.recv_mb : 0) + " MB";
}

/* ---------- state refresh ---------- */
async function refreshBrain() {
  try {
    const r = await fetch("/api/brain");
    const b = await r.json();
    const c = $("#brain-chip");
    c.textContent = b.alive ? "🧠 ONLINE" : "🧠 OFFLINE";
    c.style.color = b.alive ? "var(--green)" : "rgba(191,234,255,.4)";
  } catch (e) {}
}

async function refreshState() {
  try {
    const r = await fetch("/api/state");
    const s = await r.json();
    applyPowerState(s.online);
    $("#cmd-count").textContent = s.commands_handled;
    if (s.ears !== undefined) renderEars(s.ears);
    if (s.mode) $("#mode-chip").textContent = "MODE: " + s.mode.toUpperCase();
    if (s.ambience !== undefined) $("#amb-chip").textContent = "♩ " + s.ambience.toUpperCase();
    const rc = $("#rem-chip");
    if (s.reminders) { rc.style.display = "inline"; rc.textContent = "⏰ " + s.reminders; }
    else rc.style.display = "none";
    if (s.history && s.history.length && !state.historyHydrated) {
      s.history.slice(-15).forEach((h) => addHistory(h.text, h.reply));
      state.historyHydrated = true;
    }
  } catch (e) {}
}

/* ---------- clock ---------- */
setInterval(() => {
  const now = new Date();
  $("#clock").textContent = now.toLocaleTimeString("en-GB", { hour12: false });
}, 1000);

/* ---------- background particle field ---------- */
(function bgField() {
  const cv = $("#bg-canvas");
  const ctx = cv.getContext("2d");
  let W, H, stars = [];
  function resize() {
    W = cv.width = innerWidth; H = cv.height = innerHeight;
    stars = Array.from({ length: 130 }, () => ({
      x: Math.random() * W, y: Math.random() * H,
      r: Math.random() * 1.4 + 0.2,
      s: Math.random() * 0.25 + 0.05,
      tw: Math.random() * Math.PI * 2,
    }));
  }
  addEventListener("resize", resize); resize();
  let last = performance.now();
  (function loop(now) {
    const dt = Math.min(0.05, (now - last) / 1000); last = now;
    ctx.clearRect(0, 0, W, H);
    for (const st of stars) {
      st.tw += dt * 1.6;
      st.y -= st.s * dt * 30;
      if (st.y < -4) st.y = H + 4;
      const a = 0.25 + 0.55 * Math.abs(Math.sin(st.tw));
      ctx.beginPath();
      ctx.arc(st.x, st.y, st.r, 0, 7);
      ctx.fillStyle = `rgba(0,217,255,${a * (state.online ? 1 : 0.25)})`;
      ctx.fill();
    }
    requestAnimationFrame(loop);
  })(last);
})();

/* ---------- boot sequence ---------- */
async function boot() {
  const lines = [
    "J.A.R.V.I.S. MK VI — LOCAL INSTALLATION",
    "© STARK INDUSTRIES (PARODY) · RUNS FULLY OFFLINE",
    "",
    "NEURAL CORE ............ ONLINE",
    "SENSORY ARRAY .......... CALIBRATED",
    "SYSTEM TELEMETRY ........ SYNCED",
    "DEVICE CONTROL .......... READY",
    "VOICE INTERFACE ......... ARMED",
    "",
    "ALL SYSTEMS NOMINAL",
  ];
  const log = $("#boot-log");
  for (const l of lines) {
    const d = document.createElement("div");
    d.textContent = l || " ";
    log.appendChild(d);
    await new Promise((r) => setTimeout(r, 180));
  }
  await new Promise((r) => setTimeout(r, 500));
  $("#boot-overlay").classList.add("done");
  speak("Welcome back, sir. All systems are online.");
  startWakeListening();
}

/* ---------- init ---------- */
connectWS();
setupRecognition();
refreshState();
refreshBrain();
setInterval(refreshBrain, 15000);
boot();
