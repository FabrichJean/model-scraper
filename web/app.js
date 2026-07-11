function esc(s) {
  return String(s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

// Icônes SVG inline (style Heroicons outline) — remplacent les emojis, rendu
// cohérent sur toutes les plateformes/polices contrairement aux emojis.
const ICON_PATHS = {
  download: "M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5M16.5 12 12 16.5m0 0L7.5 12m4.5 4.5V3",
  search: "m21 21-5.197-5.197m0 0A7.5 7.5 0 1 0 5.196 5.196a7.5 7.5 0 0 0 10.607 10.607Z",
  arrowLeft: "M10.5 19.5 3 12m0 0 7.5-7.5M3 12h18",
  check: "m4.5 12.75 6 6 9-13.5",
  x: "M6 18 18 6M6 6l12 12",
  refresh: "M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0 3.181 3.183a8.25 8.25 0 0 0 13.803-3.7M4.031 9.865a8.25 8.25 0 0 1 13.803-3.7l3.181 3.182m0-4.991v4.99",
  video: "m15.75 10.5 4.72-4.72a.75.75 0 0 1 1.28.53v11.38a.75.75 0 0 1-1.28.53l-4.72-4.72M4.5 18.75h9a2.25 2.25 0 0 0 2.25-2.25v-9a2.25 2.25 0 0 0-2.25-2.25h-9A2.25 2.25 0 0 0 2.25 7.5v9a2.25 2.25 0 0 0 2.25 2.25Z",
};

function icon(name) {
  const isSpinner = name === "spinner";
  const path = ICON_PATHS[isSpinner ? "refresh" : name];
  return '<svg class="icon' + (isSpinner ? " spin" : "") + '" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">' +
    '<path stroke-linecap="round" stroke-linejoin="round" d="' + path + '"/></svg>';
}

// Si ce site (web/) est servi depuis une autre origine que server.py (autre port,
// autre domaine — d'où le CORS côté serveur), définir AVANT app.js soit :
//   window.PH_API_BASE = "http://localhost:8080"                      (un seul candidat)
//   window.PH_API_BASE = ["http://localhost:8080", "http://192.168.1.50:8080"]  (plusieurs, essayés dans l'ordre)
//   window.PH_API_BASES = [...]  (alias pluriel, accepté aussi)
// Par défaut (même origine, cas standard) : chaîne vide = URLs relatives.
// PH_API_BASE accepte indifféremment une chaîne unique ou un tableau, pour éviter
// tout piège singulier/pluriel.
const API_BASE_CANDIDATES = (
  Array.isArray(window.PH_API_BASES) && window.PH_API_BASES.length ? window.PH_API_BASES :
  Array.isArray(window.PH_API_BASE) && window.PH_API_BASE.length ? window.PH_API_BASE :
  window.PH_API_BASE ? [window.PH_API_BASE] :
  [""]
).map((b) => b.replace(/\/$/, ""));

let API_BASE = API_BASE_CANDIDATES[0];

// Sonde chaque candidat dans l'ordre de priorité (GET /api/ping, timeout court)
// et retient le premier qui répond. Tant que cette promesse n'est pas résolue,
// apiUrl() utilise le premier candidat par défaut.
const apiReady = (async function resolveApiBase(timeoutMs = 10000) {
  if (API_BASE_CANDIDATES.length === 1) { API_BASE = API_BASE_CANDIDATES[0]; return; }
  for (const base of API_BASE_CANDIDATES) {
    try {
      const ctrl = new AbortController();
      const t = setTimeout(() => ctrl.abort(), timeoutMs);
      const r = await fetch(base + "/api/ping", { signal: ctrl.signal, cache: "no-store" });
      clearTimeout(t);
      if (r.ok) { API_BASE = base; return; }
    } catch (e) { /* candidat suivant */ }
  }
  console.warn("Aucune des API_BASE candidates n'a répondu, repli sur la première:", API_BASE_CANDIDATES[0]);
})();

function apiUrl(path) {
  return API_BASE + path;
}

// fetch() + parsing JSON avec message clair si la réponse n'est pas du JSON —
// ça arrive typiquement quand l'API est sur une autre origine mal configurée
// (API_BASE) : le fetch relatif tape alors le mauvais serveur et reçoit une
// page HTML (404) au lieu de JSON, ce que JSON.parse rapporte de façon très cryptique.
async function fetchJSON(path, opts) {
  await apiReady;
  const r = await fetch(apiUrl(path), opts);
  const contentType = r.headers.get("content-type") || "";
  if (!contentType.includes("application/json")) {
    throw new Error(
      `Réponse non-JSON de ${apiUrl(path)} (HTTP ${r.status}) — ` +
      `l'API est-elle bien accessible à cette adresse ? (voir window.PH_API_BASE / PH_API_BASES)`
    );
  }
  return r.json();
}

// Pendant la résolution de l'API (sondage des candidats ci-dessus), le contenu
// reste rendu et visible en dessous — un overlay glass flouté avec spinner le
// recouvre (et bloque les clics via z-index) plutôt que de le remplacer.
const connectingOverlay = document.getElementById("connecting-overlay");
const connectingMessageEl = document.getElementById("connecting-message");

// Messages qui défilent pendant l'attente, pour que le chargement paraisse
// motivé plutôt qu'un spinner nu. Purement cosmétique — la résolution réelle
// (sondage des candidats API_BASE) tourne en parallèle, indépendamment.
const CONNECTING_MESSAGES = [
  "Vérification de la disponibilité…",
  "Choix du point d'accès le plus rapide…",
  "Établissement de la connexion sécurisée…",
  "Synchronisation des paramètres…",
  "Chargement des modules…",
  "Résolution des routes de l'API…",
  "Vérification du certificat…",
  "Préparation de l'interface…",
  "Vérification des accès…",
  "Chargement des ressources statiques…",
  "Optimisation de la connexion…",
  "Mise en cache des données…",
  "Configuration de la session…",
  "Test de latence…",
  "Alignement des horloges…",
  "Compression des échanges…",
  "Vérification de l'intégrité…",
  "Dernières vérifications…",
  "Finalisation…",
  "Presque prêt…",
];
let connectingMessageTimer = null;

function startConnectingMessages() {
  let i = 0;
  connectingMessageEl.textContent = CONNECTING_MESSAGES[0];
  connectingMessageTimer = setInterval(() => {
    i = (i + 1) % CONNECTING_MESSAGES.length;
    connectingMessageEl.classList.add("fade");
    setTimeout(() => {
      connectingMessageEl.textContent = CONNECTING_MESSAGES[i];
      connectingMessageEl.classList.remove("fade");
    }, 100);
  }, 2000);
}

function stopConnectingMessages() {
  clearInterval(connectingMessageTimer);
}

connectingOverlay.classList.add("visible");
startConnectingMessages();
apiReady.finally(() => { connectingOverlay.classList.remove("visible"); stopConnectingMessages(); });

// Miniatures Pornhub = URLs signées à courte durée de vie : certaines expirent
// avant le chargement. Remplace par le placeholder plutôt que laisser une case noire.
function onThumbError(img) {
  img.replaceWith(Object.assign(document.createElement("div"), { className: "no-img", innerHTML: "&#9654;" }));
}

// ─────────────────────────────────────────────────────────────
// Navigation entre vues
// ─────────────────────────────────────────────────────────────

const mainNav = document.getElementById("main-nav");
const scanBackLink = document.getElementById("scan-back");
const views = {
  download: document.getElementById("view-download"),
  search: document.getElementById("view-search"),
  scan: document.getElementById("view-scan"),
};

function showView(name) {
  Object.entries(views).forEach(([k, el]) => el.classList.toggle("active", k === name));
  mainNav.style.display = name === "scan" ? "none" : "flex";
  scanBackLink.style.display = name === "scan" ? "block" : "none";
  document.querySelectorAll(".nav-link").forEach(a => a.classList.toggle("active", a.dataset.view === name));
}

mainNav.addEventListener("click", (e) => {
  const link = e.target.closest(".nav-link");
  if (!link) return;
  e.preventDefault();
  showView(link.dataset.view);
  history.replaceState(null, "", "#");
});

scanBackLink.addEventListener("click", (e) => {
  e.preventDefault();
  showView("search");
  history.replaceState(null, "", "#");
});

// ─────────────────────────────────────────────────────────────
// Téléchargement (short ou vidéo complète)
// ─────────────────────────────────────────────────────────────

const downloadForm = document.getElementById("download-form");
const downloadUrlInput = document.getElementById("download-url");
const downloadJobPanel = document.getElementById("download-job");
const downloadJobUrl = document.getElementById("download-job-url");
const downloadBadge = document.getElementById("download-badge");
const downloadBar = document.getElementById("download-bar");
const downloadLog = document.getElementById("download-log");
const downloadDlWrap = document.getElementById("download-dl-wrap");

function addLogLine(box, e) {
  const d = document.createElement("div");
  d.className = "log-" + (e.level || "info");
  d.innerHTML = '<span class="log-t">' + (e.t || "") + "</span>" + esc(e.msg || "");
  box.appendChild(d);
  box.scrollTop = box.scrollHeight;
}

async function startDownload(url) {
  downloadJobUrl.textContent = url;
  downloadJobPanel.style.display = "block";
  downloadForm.style.display = "none";
  downloadBadge.innerHTML = icon("spinner") + "En cours…";
  downloadBadge.className = "badge badge-running";
  downloadBar.style.width = "0%";
  downloadLog.innerHTML = "";
  downloadDlWrap.innerHTML = "";

  let job_id;
  try {
    const fd = new FormData();
    fd.append("url", url);
    const data = await fetchJSON("/api/start", { method: "POST", body: fd });
    if (!data.job_id) throw new Error(data.error || "Erreur");
    job_id = data.job_id;
  } catch (e) {
    downloadBadge.innerHTML = icon("x") + "Erreur";
    downloadBadge.className = "badge badge-error";
    addLogLine(downloadLog, { level: "error", msg: e.message });
    return;
  }

  history.replaceState(null, "", "#dl=" + job_id);
  watchDownloadJob(job_id);
}

function watchDownloadJob(job_id) {
  downloadJobPanel.style.display = "block";
  downloadForm.style.display = "none";
  const es = new EventSource(apiUrl("/stream/" + job_id));
  es.onmessage = function (e) {
    const ev = JSON.parse(e.data);
    if (ev.type === "log") addLogLine(downloadLog, ev);
    else if (ev.type === "progress") downloadBar.style.width = ev.value + "%";
    else if (ev.type === "done") {
      downloadBar.style.width = "100%";
      downloadBadge.innerHTML = icon("check") + "Terminé";
      downloadBadge.className = "badge badge-done";
      downloadDlWrap.innerHTML = '<a class="dl-btn" href="' + apiUrl("/file/" + encodeURIComponent(ev.file)) + '">' + icon("download") + "Télécharger — " + esc(ev.file) + "</a>";
      es.close();
    } else if (ev.type === "error") {
      downloadBadge.innerHTML = icon("x") + "Erreur";
      downloadBadge.className = "badge badge-error";
      addLogLine(downloadLog, { level: "error", msg: ev.msg || "Erreur" });
      es.close();
    }
  };
  es.onerror = function () {
    if (downloadBadge.className.includes("running")) addLogLine(downloadLog, { level: "warn", msg: "Reconnexion SSE…" });
  };
}

downloadForm.addEventListener("submit", (e) => {
  e.preventDefault();
  const url = downloadUrlInput.value.trim();
  if (url) startDownload(url);
});

// ─────────────────────────────────────────────────────────────
// Recherche de modèle par mot-clé
// ─────────────────────────────────────────────────────────────

const searchForm = document.getElementById("search-form");
const searchQInput = document.getElementById("search-q");
const searchResults = document.getElementById("search-results");

function renderModelResults(models) {
  const grid = document.createElement("div");
  grid.className = "search-grid";
  models.forEach((r) => {
    const card = document.createElement("div");
    card.className = "scard";
    const badgeCls = r.type === "model" ? "badge-model" : "badge-pornstar";
    card.innerHTML =
      '<div class="scard-name">' + esc(r.name) + '</div>' +
      '<div class="scard-meta"><span class="scard-badge ' + badgeCls + '">' + esc(r.type) + '</span></div>' +
      '<button class="scard-btn">' + icon("video") + "Scanner ses shorts</button>";
    card.querySelector(".scard-btn").addEventListener("click", () => startScan(r.url));
    grid.appendChild(card);
  });
  return grid;
}

function renderVideoResults(videos) {
  const grid = document.createElement("div");
  grid.className = "grid";
  videos.forEach((v) => {
    const thumb = v.thumbnail
      ? '<img src="' + esc(v.thumbnail) + '" loading="lazy" referrerpolicy="no-referrer" alt="" onerror="onThumbError(this)">'
      : '<div class="no-img">&#9654;</div>';
    const card = document.createElement("div");
    card.className = "vcard";
    card.innerHTML =
      '<div class="vcard-thumb">' + thumb + (v.duration ? '<span class="dur">' + esc(v.duration) + "</span>" : "") + "</div>" +
      '<div class="vcard-body">' +
        '<div class="vcard-title">' + esc(v.title || "—") + "</div>" +
        '<button class="vcard-dl-btn">' + icon("download") + "Télécharger</button>" +
        '<div class="vcard-prog" style="display:none">' +
          '<div class="vcard-prog-wrap"><div class="vcard-prog-bar"></div></div>' +
          '<div class="vcard-prog-label">En attente…</div>' +
        "</div>" +
      "</div>";
    card.querySelector(".vcard-dl-btn").addEventListener("click", () => startCardDownload(v.url, card));
    grid.appendChild(card);
  });
  return grid;
}

function renderSearchResults(models, videos) {
  searchResults.innerHTML = "";
  if (!models.length && !videos.length) {
    searchResults.innerHTML = '<p class="no-results">Aucun résultat.</p>';
    return;
  }
  if (models.length) {
    const h = document.createElement("h2");
    h.className = "results-heading";
    h.textContent = "Modèles & pornstars";
    searchResults.appendChild(h);
    searchResults.appendChild(renderModelResults(models));
  }
  if (videos.length) {
    const h = document.createElement("h2");
    h.className = "results-heading";
    h.textContent = "Vidéos";
    searchResults.appendChild(h);
    searchResults.appendChild(renderVideoResults(videos));
  }
}

async function runSearch(q) {
  searchResults.innerHTML = '<p class="no-results">Recherche…</p>';
  try {
    const data = await fetchJSON("/api/search?q=" + encodeURIComponent(q));
    if (data.error) throw new Error(data.error);
    renderSearchResults(data.models || [], data.videos || []);
  } catch (e) {
    searchResults.innerHTML = '<p class="search-error">Erreur: ' + esc(e.message) + "</p>";
  }
}

searchForm.addEventListener("submit", (e) => {
  e.preventDefault();
  const q = searchQInput.value.trim();
  if (q) runSearch(q);
});

// ─────────────────────────────────────────────────────────────
// Scan des shorts d'un modèle (déclenché depuis un résultat de recherche)
// ─────────────────────────────────────────────────────────────

const scanUrlLabel = document.getElementById("scan-url");
const scanBadge = document.getElementById("scan-badge");
const scanLog = document.getElementById("scan-log");
const scanStats = document.getElementById("scan-stats");
const scanSubs = document.getElementById("scan-subs");
const scanCount = document.getElementById("scan-count");
const scanDlAllBtn = document.getElementById("scan-dl-all");
const scanGrid = document.getElementById("scan-grid");

async function startScan(modelUrl) {
  showView("scan");
  scanUrlLabel.textContent = modelUrl;
  scanBadge.innerHTML = icon("spinner") + "Scan en cours…";
  scanBadge.className = "badge badge-running";
  scanLog.innerHTML = "";
  scanLog.style.display = "block";
  scanLog.style.opacity = "1";
  scanStats.style.display = "none";
  scanGrid.innerHTML = "";

  let job_id;
  try {
    const fd = new FormData();
    fd.append("url", modelUrl);
    const data = await fetchJSON("/api/model/scan", { method: "POST", body: fd });
    if (!data.job_id) throw new Error(data.error || "Erreur");
    job_id = data.job_id;
  } catch (e) {
    scanBadge.innerHTML = icon("x") + "Erreur";
    scanBadge.className = "badge badge-error";
    addLogLine(scanLog, { level: "error", msg: e.message });
    return;
  }

  history.replaceState(null, "", "#scan=" + job_id + "&url=" + encodeURIComponent(modelUrl));
  watchScanJob(job_id);
}

function watchScanJob(job_id) {
  const es = new EventSource(apiUrl("/stream/model/" + job_id));
  es.onmessage = function (e) {
    const ev = JSON.parse(e.data);
    if (ev.type === "log") addLogLine(scanLog, ev);
    else if (ev.type === "done") {
      const n = ev.count || (ev.videos || []).length;
      scanBadge.innerHTML = icon("check") + n + " vidéo" + (n > 1 ? "s" : "") + " trouvée" + (n > 1 ? "s" : "");
      scanBadge.className = "badge badge-done";
      if (ev.videos && ev.videos.length) renderScanVideos(ev.videos, ev.subscribers || "", ev.video_count || "");
      es.close();
    } else if (ev.type === "error") {
      scanBadge.innerHTML = icon("x") + "Erreur";
      scanBadge.className = "badge badge-error";
      es.close();
    }
  };
  es.onerror = function () {
    if (scanBadge.className.includes("running")) addLogLine(scanLog, { level: "warn", msg: "Reconnexion SSE…" });
  };
}

function renderScanVideos(videos, subscribers, videoCount) {
  scanLog.style.transition = "opacity .4s";
  scanLog.style.opacity = "0";
  setTimeout(() => { scanLog.style.display = "none"; }, 400);

  scanStats.style.display = "flex";
  if (subscribers) scanSubs.textContent = subscribers.replace(" Subscribers", "").replace(" subscribers", "");
  if (videoCount) scanCount.textContent = videoCount.replace(" Videos", "").replace(" videos", "");
  scanDlAllBtn.style.display = videos.length ? "inline-block" : "none";

  scanGrid.innerHTML = "";
  videos.forEach((v) => {
    const link = v.link || "";
    const img = v.imageUrl || "";
    const thumb = img
      ? '<img src="' + esc(img) + '" loading="lazy" referrerpolicy="no-referrer" alt="" onerror="onThumbError(this)">'
      : '<div class="no-img">&#9654;</div>';
    const card = document.createElement("div");
    card.className = "vcard";
    card.innerHTML =
      '<div class="vcard-thumb">' + thumb + (v.duration ? '<span class="dur">' + esc(v.duration) + "</span>" : "") + "</div>" +
      '<div class="vcard-body">' +
        '<div class="vcard-title">' + esc(v.title || "—") + "</div>" +
        '<button class="vcard-dl-btn">' + icon("download") + "Télécharger</button>" +
        '<div class="vcard-prog" style="display:none">' +
          '<div class="vcard-prog-wrap"><div class="vcard-prog-bar"></div></div>' +
          '<div class="vcard-prog-label">En attente…</div>' +
        "</div>" +
      "</div>";
    card.querySelector(".vcard-dl-btn").addEventListener("click", () => startCardDownload(link, card));
    scanGrid.appendChild(card);
  });
}

async function startCardDownload(url, card) {
  const btn = card.querySelector(".vcard-dl-btn");
  const prog = card.querySelector(".vcard-prog");
  const bar = card.querySelector(".vcard-prog-bar");
  const lbl = card.querySelector(".vcard-prog-label");

  btn.disabled = true;
  btn.innerHTML = icon("spinner") + "Démarrage…";

  let job_id;
  try {
    const fd = new FormData();
    fd.append("url", url);
    const data = await fetchJSON("/api/start", { method: "POST", body: fd });
    if (!data.job_id) throw new Error(data.error || "Erreur");
    job_id = data.job_id;
  } catch (e) {
    btn.disabled = false;
    btn.innerHTML = icon("download") + "Télécharger";
    lbl.textContent = "Erreur: " + e.message;
    lbl.className = "vcard-prog-label err";
    prog.style.display = "block";
    return;
  }

  btn.style.display = "none";
  prog.style.display = "block";
  lbl.textContent = "En cours…";

  const es = new EventSource(apiUrl("/stream/" + job_id));
  es.onmessage = function (e) {
    const ev = JSON.parse(e.data);
    if (ev.type === "progress") {
      bar.style.width = ev.value + "%";
      lbl.textContent = Math.round(ev.value) + "%";
    } else if (ev.type === "log") {
      const msg = (ev.msg || "").replace(/^\[.*?\]\s*/, "");
      if (msg.length < 60) lbl.textContent = msg;
    } else if (ev.type === "done") {
      bar.style.width = "100%";
      lbl.innerHTML = icon("check") + "Terminé";
      lbl.className = "vcard-prog-label done";
      const a = document.createElement("a");
      a.className = "vcard-save-btn";
      a.href = apiUrl("/file/" + encodeURIComponent(ev.file));
      a.innerHTML = icon("download") + "Sauvegarder";
      prog.after(a);
      es.close();
    } else if (ev.type === "error") {
      lbl.innerHTML = icon("x") + "Erreur";
      lbl.className = "vcard-prog-label err";
      btn.style.display = "block";
      btn.disabled = false;
      btn.innerHTML = icon("refresh") + "Réessayer";
      es.close();
    }
  };
  es.onerror = function () { lbl.textContent = "Reconnexion…"; };
}

scanDlAllBtn.addEventListener("click", () => {
  const cards = [...scanGrid.querySelectorAll(".vcard")];
  const pending = cards.filter((c) => { const b = c.querySelector(".vcard-dl-btn"); return b && !b.disabled; });
  if (!pending.length) return;
  if (!confirm("Lancer le téléchargement de " + pending.length + " vidéo" + (pending.length > 1 ? "s" : "") + " ?")) return;
  pending.forEach((card, i) => {
    setTimeout(() => {
      const btn = card.querySelector(".vcard-dl-btn");
      if (btn && !btn.disabled) btn.click();
    }, i * 500);
  });
});

// ─────────────────────────────────────────────────────────────
// Reprise d'un job en cours après rechargement de page (#dl=<id> / #scan=<id>&url=...)
// ─────────────────────────────────────────────────────────────

(function resumeFromHash() {
  const hash = location.hash.slice(1);
  if (!hash) return;
  const params = new URLSearchParams(hash);
  const dlJobId = params.get("dl");
  const scanJobId = params.get("scan");

  if (dlJobId) {
    watchDownloadJob(dlJobId);
  } else if (scanJobId) {
    showView("scan");
    scanUrlLabel.textContent = params.get("url") || "";
    watchScanJob(scanJobId);
  }
})();
