/* Transcription de cours — logique de la page. */
"use strict";

const $ = (id) => document.getElementById(id);

const state = {
  jobs: [],
  selected: null,
  detail: null,
  pending: [],
  settings: {},
  blocks: [],
  blocksJobId: null,
  editingBlockId: null,
  editingOriginalText: "",
  activeBlockId: null,
  playerSource: null, // "video" | "audio" | null
  playerJobId: null,
  playerSpeed: 1,
};

/* ----------------------------------------------------------- utilitaires */

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function clock(seconds) {
  const total = Math.max(0, Math.round(Number(seconds) || 0));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const mm = String(m).padStart(h ? 2 : 1, "0");
  return h ? `${h}:${mm}:${String(s).padStart(2, "0")}`
           : `${mm}:${String(s).padStart(2, "0")}`;
}

function humanSize(bytes) {
  const units = ["o", "ko", "Mo", "Go"];
  let value = Number(bytes) || 0;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit += 1; }
  return `${value.toFixed(value >= 10 || unit === 0 ? 0 : 1)} ${units[unit]}`;
}

function humanDate(iso) {
  if (!iso) return "";
  const date = new Date(iso);
  return Number.isNaN(date.getTime())
    ? ""
    : date.toLocaleString("fr-FR", { dateStyle: "medium", timeStyle: "short" });
}

let toastTimer;
function toast(message, isError = false) {
  const node = $("toast");
  node.textContent = message;
  node.classList.toggle("error", isError);
  node.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { node.hidden = true; }, 4200);
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let detail = `Erreur ${response.status}`;
    try {
      const body = await response.json();
      if (body.detail) detail = body.detail;
    } catch (_) { /* réponse non JSON */ }
    throw new Error(detail);
  }
  return response.status === 204 ? null : response.json();
}

/* ------------------------------------------------------------- démarrage */

async function loadStatus() {
  const status = await api("/api/status");
  state.settings = status.settings;

  const engineSelect = $("engine");
  engineSelect.innerHTML = "";
  Object.entries(status.engines).forEach(([name, info]) => {
    const option = document.createElement("option");
    option.value = name;
    option.textContent = info.available ? info.label : `${info.label} — indisponible`;
    option.disabled = !info.available;
    option.dataset.detail = info.detail;
    engineSelect.append(option);
  });
  const preferred = status.engines[status.settings.default_engine];
  engineSelect.value = preferred && preferred.available
    ? status.settings.default_engine
    : (Object.entries(status.engines).find(([, i]) => i.available) || ["local"])[0];
  updateEngineDetail();

  const modelSelect = $("model");
  modelSelect.innerHTML = "";
  status.models.forEach((model) => {
    const option = document.createElement("option");
    option.value = model;
    option.textContent = model;
    modelSelect.append(option);
  });
  modelSelect.value = status.settings.default_model;

  $("language").value = status.settings.language ?? "fr";
  $("structure").checked = Boolean(status.settings.structure_output);

  const claude = status.proofread.claude;
  const proofSelect = $("proofread");
  proofSelect.options[0].disabled = !claude.available;
  proofSelect.value = claude.available ? status.settings.default_proofread : "basic";
  $("proofread-detail").textContent = claude.available
    ? claude.detail
    : `${claude.detail} La relecture simple reste disponible.`;

  $("factcheck").checked = Boolean(status.settings.factcheck) && claude.available;
  $("factcheck").disabled = !claude.available;
  const obsidian = status.obsidian;
  $("publish").checked = obsidian.available;
  $("publish").disabled = !obsidian.available;
  $("one-click-btn").title = obsidian.available
    ? ""
    : `Publication Obsidian désactivée : ${obsidian.detail}`;

  const pills = [
    pill(status.ffmpeg.available, "Extraction audio", status.ffmpeg.detail),
    pill(status.engines.local.available, "Moteur local", status.engines.local.detail),
    pill(status.engines.runpod.available, "RunPod", status.engines.runpod.detail, true),
    pill(claude.available, "Claude", claude.detail, true),
    pill(obsidian.available, "Obsidian", obsidian.detail, true),
  ];
  $("health").innerHTML = pills.join("");
}

function pill(ok, label, detail, optional = false) {
  const cls = ok ? "ok" : (optional ? "warn" : "off");
  return `<span class="pill ${cls}" title="${escapeHtml(detail)}">${escapeHtml(label)}</span>`;
}

function updateEngineDetail() {
  const option = $("engine").selectedOptions[0];
  $("engine-detail").textContent = option ? option.dataset.detail || "" : "";
}

/* ------------------------------------------------------------- dépôt */

function setPending(files) {
  state.pending = Array.from(files || []);
  const label = $("pending-files");
  if (!state.pending.length) {
    label.textContent = "";
  } else if (state.pending.length === 1) {
    const file = state.pending[0];
    label.textContent = `${file.name} — ${humanSize(file.size)}`;
  } else {
    const total = state.pending.reduce((sum, f) => sum + f.size, 0);
    label.textContent = `${state.pending.length} fichiers — ${humanSize(total)}`;
  }
  $("submit-btn").disabled = state.pending.length === 0;
  $("one-click-btn").disabled = state.pending.length === 0;
}

function initDropzone() {
  const zone = $("dropzone");
  const input = $("file-input");

  zone.addEventListener("click", () => input.click());
  zone.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") { event.preventDefault(); input.click(); }
  });
  input.addEventListener("change", () => setPending(input.files));

  ["dragenter", "dragover"].forEach((type) =>
    zone.addEventListener(type, (event) => {
      event.preventDefault();
      zone.classList.add("is-over");
    })
  );
  ["dragleave", "drop"].forEach((type) =>
    zone.addEventListener(type, (event) => {
      event.preventDefault();
      zone.classList.remove("is-over");
    })
  );
  zone.addEventListener("drop", (event) => {
    if (event.dataTransfer?.files?.length) setPending(event.dataTransfer.files);
  });
}

async function submitFiles(event, oneClick = false) {
  if (event) event.preventDefault();
  if (!state.pending.length) return;

  const button = oneClick ? $("one-click-btn") : $("submit-btn");
  const otherButton = oneClick ? $("submit-btn") : $("one-click-btn");
  const originalLabel = button.textContent;
  button.disabled = true;
  otherButton.disabled = true;
  const files = state.pending.slice();
  let sent = 0;

  for (const file of files) {
    const form = new FormData();
    form.append("file", file);
    form.append("one_click", oneClick ? "true" : "false");
    form.append("engine", $("engine").value);
    form.append("model", $("model").value);
    form.append("language", $("language").value);
    form.append("proofread", $("proofread").value);
    form.append("structure", $("structure").checked ? "true" : "false");
    form.append("verify", $("verify").checked ? "true" : "false");
    form.append("chain", $("chain").checked ? "true" : "false");
    form.append("factcheck", $("factcheck").checked ? "true" : "false");
    form.append("publish", $("publish").checked ? "true" : "false");

    button.textContent = files.length > 1
      ? `Envoi ${sent + 1}/${files.length}…`
      : "Envoi…";
    try {
      const job = await api("/api/jobs", { method: "POST", body: form });
      sent += 1;
      if (!state.selected) state.selected = job.id;
    } catch (error) {
      toast(`${file.name} : ${error.message}`, true);
    }
  }

  button.textContent = originalLabel;
  setPending([]);
  $("file-input").value = "";
  if (sent) {
    toast(sent > 1 ? `${sent} fichiers mis en file.` : "Traitement lancé.");
    refreshJobs();
  }
}

/* --------------------------------------------------------------- file */

function statusLabel(job) {
  return {
    queued: "En attente",
    running: job.stage || "En cours",
    transcribed: "Transcrit — à relire",
    done: "Relu",
    checked: "Vérifié",
    published: "Publié",
    error: "Erreur",
    canceled: "Annulé",
  }[job.status] || job.status;
}

// Un travail transcrit est déjà exploitable : texte brut, segments et
// sous-titres sont disponibles, la relecture peut venir plus tard.
function isReadable(job) {
  return ["transcribed", "done", "checked", "published"].includes(job.status);
}

// Un travail relu (ou davantage) a un texte relu à vérifier ou publier.
function isProofread(job) {
  return ["done", "checked", "published"].includes(job.status);
}

const PUBLICATION_FLAGS = {
  publie: "Publié dans Obsidian",
  pret: "Prêt à publier",
};

function jobFlags(job) {
  const flags = [];
  if (job.pending_review_count) {
    flags.push(`<span class="flag flag-review">${job.pending_review_count} à vérifier</span>`);
  }
  const pubLabel = PUBLICATION_FLAGS[job.publication_status];
  if (pubLabel) {
    flags.push(`<span class="flag flag-pub-${job.publication_status}">${pubLabel}</span>`);
  }
  return flags.join("");
}

function renderJobs() {
  const list = $("job-list");
  const jobs = state.jobs;
  $("jobs-empty").hidden = jobs.length > 0;

  list.innerHTML = jobs.map((job) => {
    const percent = Math.round((job.progress || 0) * 100);
    const active = job.id === state.selected ? " is-active" : "";
    const showBar = job.status === "running" || job.status === "queued";
    return `
      <li class="job${active}" data-id="${job.id}" tabindex="0">
        <div class="job-name" title="${escapeHtml(job.filename)}">
          ${escapeHtml(job.title || job.filename)}
        </div>
        <div class="job-meta">
          <span class="job-stage">${escapeHtml(statusLabel(job))}</span>
          <span class="job-date">${escapeHtml(humanDate(job.created_at))}</span>
        </div>
        <div class="job-meta">
          <span class="badge ${job.status}">${
            job.duration ? clock(job.duration) : humanSize(job.size_bytes)
          }</span>
          ${jobFlags(job)}
        </div>
        ${showBar ? `<div class="bar"><span style="width:${percent}%"></span></div>` : ""}
      </li>`;
  }).join("");

  list.querySelectorAll(".job").forEach((node) => {
    const select = () => selectJob(node.dataset.id);
    node.addEventListener("click", select);
    node.addEventListener("keydown", (event) => {
      if (event.key === "Enter") select();
    });
  });
}

async function refreshJobs() {
  const query = $("search").value.trim();
  const path = query ? `/api/jobs?q=${encodeURIComponent(query)}` : "/api/jobs";
  const data = await api(path);
  applyJobs(data.jobs);
}

function applyJobs(jobs) {
  const previous = new Map(state.jobs.map((job) => [job.id, job.status]));
  state.jobs = jobs;
  renderJobs();

  // Rafraîchir le détail affiché quand le travail vient de se terminer.
  if (state.selected) {
    const current = jobs.find((job) => job.id === state.selected);
    if (current && previous.get(current.id) !== current.status) {
      selectJob(state.selected, true);
    } else if (current && current.status !== "done") {
      renderDetailHeader(current);
    }
  }
}

function listenEvents() {
  const source = new EventSource("/api/events");
  source.onmessage = (event) => {
    if ($("search").value.trim()) return;  // ne pas écraser une recherche
    try {
      applyJobs(JSON.parse(event.data).jobs);
    } catch (_) { /* trame incomplète */ }
  };
  source.onerror = () => { /* EventSource se reconnecte tout seul */ };
}

/* ----------------------------------------------------------- résultat */

async function selectJob(jobId, keepTab = false) {
  if (state.selected !== jobId) {
    state.editingBlockId = null;
    state.activeBlockId = null;
  }
  state.selected = jobId;
  if (!keepTab) {
    // Sur petit écran, choisir un travail bascule vers le panneau éditeur —
    // mais seulement pour une vraie sélection : un rafraîchissement de fond
    // (keepTab, déclenché par le SSE quand un statut change) ne doit pas
    // arracher l'utilisateur du panneau qu'il est en train de lire.
    document.querySelector('.shell-tab[data-shell="workspace"]')?.click();
  }
  renderJobs();
  try {
    state.detail = await api(`/api/jobs/${jobId}`);
    renderDetail();
  } catch (error) {
    toast(error.message, true);
  }
}

function renderDetailHeader(job) {
  if (!state.detail || state.detail.id !== job.id) return;
  Object.assign(state.detail, job);
  $("result-meta").textContent = metaLine(state.detail);
}

function metaLine(job) {
  const parts = [statusLabel(job)];
  if (job.duration) parts.push(clock(job.duration));
  parts.push(`${job.engine === "runpod" ? "RunPod" : "local"} · ${job.model}`);
  if (job.proofread === "claude") parts.push("relu par Claude");
  else if (job.proofread === "basic") parts.push("relecture simple");
  parts.push(humanDate(job.created_at));
  return parts.filter(Boolean).join(" · ");
}

function renderTranscript(text, extraClass = "") {
  if (!text) return `<p class="empty">Rien à afficher.</p>`;
  const blocks = text.split(/\n{2,}/).map((block) => {
    const trimmed = block.trim();
    if (!trimmed) return "";
    if (trimmed.startsWith("## ")) return `<h3>${escapeHtml(trimmed.slice(3))}</h3>`;
    if (trimmed.startsWith("# ")) return `<h3>${escapeHtml(trimmed.slice(2))}</h3>`;
    return `<p>${escapeHtml(trimmed)}</p>`;
  });
  return `<div class="transcript ${extraClass}">${blocks.join("")}</div>`;
}

// Chemin absolu (au sens du système de fichiers) de la fiche publiée,
// pour « obsidian://open?path=… ». job.obsidian_path est relatif au coffre
// et utilise toujours « / » (voir obsidian.publish côté serveur) ; le coffre
// lui-même peut être saisi avec des « \ » sous Windows, d'où la conversion.
function obsidianAbsolutePath(job) {
  const vaultPath = state.settings.obsidian_vault_path || "";
  const sep = vaultPath.includes("\\") ? "\\" : "/";
  const relative = job.obsidian_path.split("/").join(sep);
  const vault = vaultPath.replace(/[\\/]+$/, "");
  return `${vault}${sep}${relative}`;
}

function renderDetail() {
  const job = state.detail;
  $("result-empty").hidden = Boolean(job);
  $("result-body").hidden = !job;
  findingRegistry = [];
  if (!job) return;

  $("result-title").textContent = job.title || job.filename;
  $("result-meta").textContent = metaLine(job);

  const errorBanner = $("result-error");
  errorBanner.hidden = !job.error;
  errorBanner.textContent = job.error || "";

  const summary = Array.isArray(job.summary) ? job.summary : [];
  $("result-summary").hidden = summary.length === 0;
  $("summary-list").innerHTML = summary.map((point) => `<li>${escapeHtml(point)}</li>`).join("");

  loadPlayerForJob(job);
  loadReviewBlocks(job);
  renderComparisonPanel(job);
  $("blocks-zone").hidden = false;

  renderVerification(job);
  renderSources(job);

  // Chaque étape est à part : on peut la lancer, ou la relancer avec
  // d'autres réglages, sur n'importe quel travail déjà à l'étape d'avant.
  const proofreadBtn = $("proofread-btn");
  proofreadBtn.hidden = !isReadable(job);
  proofreadBtn.textContent = isProofread(job) ? "Relancer la relecture" : "Relire maintenant";

  const factcheckBtn = $("factcheck-btn");
  factcheckBtn.hidden = !isProofread(job);
  factcheckBtn.textContent =
    job.status === "checked" || job.status === "published"
      ? "Revérifier par recherche web"
      : "Vérifier par recherche web";

  const publishBtn = $("publish-btn");
  publishBtn.hidden = !isProofread(job);
  publishBtn.textContent = job.status === "published" ? "Republier" : "Publier";

  const obsidianLink = $("obsidian-link");
  if (job.obsidian_path && state.settings.obsidian_vault_path) {
    obsidianLink.hidden = false;
    // « vault=<nom> » suppose que le nom du coffre dans Obsidian est le nom
    // du dossier — faux dès que le coffre a été renommé depuis l'appli
    // (« Vault not found »). « path=<chemin absolu> » identifie le fichier
    // sans passer par ce nom : Obsidian retrouve seul le coffre concerné.
    obsidianLink.href = `obsidian://open?path=${encodeURIComponent(obsidianAbsolutePath(job))}`;
  } else {
    obsidianLink.hidden = true;
  }

}

const KIND_LABELS = {
  omission: "omission",
  ajout: "ajout",
  sens: "sens",
  terme: "terme",
  chiffre: "chiffre",
  coupure: "coupure",
  fait: "fait",
  source: "source",
  lexique: "lexique",
};

const SOURCE_KINDS = new Set(["fait", "source"]);

function sourceLabel(point) {
  return { claude: "Claude", web: "Recherche web", lexique: "Lexique" }[point.source] || "Règle";
}

const CLAIM_TYPE_LABELS = {
  nom_propre: "nom propre",
  organisme: "organisme",
  rapport: "rapport",
  statistique: "statistique",
  reference_juridique: "référence juridique",
  date: "date",
};

function renderPendingCard(item) {
  const sources = (item.sources || []).filter((s) => s.url);
  return `
    <div class="finding pending-correction" data-correction-id="${escapeHtml(item.id)}">
      <div class="finding-head">
        <time>${clock(item.start)}</time>
        <span class="finding-kind">${escapeHtml(CLAIM_TYPE_LABELS[item.claim_type] || item.claim_type)}</span>
        <span class="finding-kind">confiance ${escapeHtml(item.confiance)}</span>
      </div>
      <p class="finding-message">« ${escapeHtml(item.citation)} » → proposé : <b>${escapeHtml(item.proposition)}</b></p>
      ${item.explication ? `<p class="meta">${escapeHtml(item.explication)}</p>` : ""}
      ${sources.length ? `
        <div class="finding-sources">
          ${sources.map((s) => `<a href="${escapeHtml(s.url)}" target="_blank" rel="noopener">${escapeHtml(s.titre || s.url)}</a>`).join("")}
        </div>` : ""}
      <div class="pending-actions">
        <button type="button" class="btn btn-primary btn-mini" data-pending-action="valider">Valider</button>
        <button type="button" class="btn btn-ghost btn-mini" data-pending-action="rejeter">Rejeter</button>
      </div>
    </div>`;
}

// Réinitialisé à chaque renderDetail() ; permet au bouton « Voir le passage »
// de retrouver le point d'origine sans le sérialiser dans le DOM.
let findingRegistry = [];

function renderFindingCard(point) {
  const idx = findingRegistry.push(point) - 1;
  const hasExcerpt = Boolean(point.raw_excerpt || point.clean_excerpt);
  return `
    <div class="finding ${escapeHtml(point.severity)}">
      <div class="finding-head">
        <time>${clock(point.start)}</time>
        <span class="finding-kind">${escapeHtml(KIND_LABELS[point.kind] || point.kind)}</span>
        <span class="finding-kind">${escapeHtml(sourceLabel(point))}</span>
        ${hasExcerpt ? `<button type="button" class="finding-expand" data-idx="${idx}">✉ Voir le passage</button>` : ""}
      </div>
      <p class="finding-message">${escapeHtml(point.message)}</p>
      ${hasExcerpt ? `
        <div class="finding-quotes">
          ${point.raw_excerpt ? `<div class="finding-quote"><b>Brut</b><span>${escapeHtml(point.raw_excerpt)}</span></div>` : ""}
          ${point.clean_excerpt ? `<div class="finding-quote"><b>Relu</b><span>${escapeHtml(point.clean_excerpt)}</span></div>` : ""}
        </div>` : ""}
    </div>`;
}

// Enlève les « … » de troncature ajoutés par les extraits côté serveur —
// ils ne font pas partie du texte à retrouver.
function stripEllipses(text) {
  return String(text ?? "").replace(/^…+/, "").replace(/…+$/, "").trim();
}

function normalizeForSearch(text) {
  return String(text ?? "").split(/\s+/).filter(Boolean).join(" ");
}

// Retrouve ``excerpt`` dans ``fullText`` et renvoie une fenêtre de contexte
// plus large autour, avec le passage mis en évidence. S'il apparaît plusieurs
// fois, on garde l'occurrence la plus proche de la position estimée par
// l'horodatage — le texte complet est déjà chargé côté client, inutile
// d'appeler le serveur pour ça.
function findPassageWindow(fullText, excerpt, approxFraction, windowSize = 320) {
  const haystack = normalizeForSearch(fullText);
  const needle = stripEllipses(excerpt);
  if (!haystack || !needle) return null;

  const approxPos = approxFraction == null ? null : approxFraction * haystack.length;
  let bestIndex = -1;
  let bestDistance = Infinity;
  let from = 0;
  for (;;) {
    const found = haystack.indexOf(needle, from);
    if (found === -1) break;
    const distance = approxPos == null ? 0 : Math.abs(found - approxPos);
    if (distance < bestDistance) {
      bestDistance = distance;
      bestIndex = found;
    }
    from = found + 1;
  }
  if (bestIndex === -1) return null;

  const start = Math.max(0, bestIndex - windowSize / 2);
  const end = Math.min(haystack.length, bestIndex + needle.length + windowSize / 2);
  const before = escapeHtml(haystack.slice(start, bestIndex));
  const marked = escapeHtml(haystack.slice(bestIndex, bestIndex + needle.length));
  const after = escapeHtml(haystack.slice(bestIndex + needle.length, end));
  return (start > 0 ? "…" : "") + before + `<mark>${marked}</mark>` + after + (end < haystack.length ? "…" : "");
}

function openPassage(point) {
  const job = state.detail;
  if (!job) return;
  const fraction = job.duration ? point.start / job.duration : null;

  $("passage-title").textContent = point.message || "";

  const rawHtml = point.raw_excerpt ? findPassageWindow(job.raw_text, point.raw_excerpt, fraction) : null;
  const cleanHtml = point.clean_excerpt ? findPassageWindow(job.clean_text, point.clean_excerpt, fraction) : null;

  $("passage-raw").innerHTML = rawHtml
    || `<span class="empty">${point.raw_excerpt ? "Passage introuvable dans le texte brut complet." : "Rien à comparer côté brut."}</span>`;
  $("passage-clean").innerHTML = cleanHtml
    || `<span class="empty">${point.clean_excerpt ? "Passage introuvable dans le texte relu complet." : "Position non localisable dans le texte relu : à vérifier à l'oreille."}</span>`;

  $("passage-dialog").showModal();
}

function renderVerification(job) {
  const rapport = job.verification || {};
  const allPoints = Array.isArray(rapport.findings) ? rapport.findings : [];
  // Les points de fidélité (règles + lecture par Claude) ici ; le fact-check
  // par recherche web a son propre onglet (voir renderSources).
  const points = allPoints.filter((p) => !SOURCE_KINDS.has(p.kind));
  const badge = $("verify-count");
  const graves = points.filter((p) => p.severity === "haute").length;

  badge.hidden = points.length === 0;
  badge.textContent = String(points.length);
  badge.style.background = graves ? "var(--danger)" : "var(--warn)";

  const panel = $("panel-verify");

  if (!isReadable(job)) {
    panel.innerHTML = `<p class="empty">Vérification disponible après la relecture.</p>`;
    return;
  }
  if (!rapport.mode) {
    panel.innerHTML = `<p class="empty">
      Ce travail n'a pas encore été relu, donc rien n'a été vérifié.
    </p>`;
    return;
  }

  const entete = `
    <div class="verify-summary">
      <span class="badge">${rapport.checked_pairs || 0} passages comparés</span>
      <span class="badge">${
        rapport.mode === "claude" ? "règles + lecture par Claude" : "règles seules"
      }</span>
      ${points.length
        ? `<span class="badge error">${graves} point${graves > 1 ? "s" : ""} à regarder de près</span>`
        : `<span class="verify-ok">Aucun écart détecté</span>`}
    </div>`;

  if (!points.length) {
    panel.innerHTML = `${entete}
      <p class="meta">
        Aucune différence de sens n'a été relevée entre le texte brut et le
        texte relu. Une vérification automatique reste une aide, pas une
        garantie : sur un passage décisif, l'audio fait foi.
      </p>`;
    return;
  }

  panel.innerHTML = entete + points.map(renderFindingCard).join("");
}

function renderSources(job) {
  const rapport = job.verification || {};
  const allPoints = Array.isArray(rapport.findings) ? rapport.findings : [];
  const points = allPoints.filter((p) => SOURCE_KINDS.has(p.kind));
  const report = job.factcheck_report;
  const enAttente = (report && Array.isArray(report.pending) ? report.pending : [])
    .filter((p) => p.status === "attente");

  const badge = $("sources-count");
  const badgeCount = points.length + enAttente.length;
  badge.hidden = badgeCount === 0;
  badge.textContent = String(badgeCount);

  const panel = $("panel-sources");

  if (!isProofread(job)) {
    panel.innerHTML = `<p class="empty">Vérification par recherche web disponible après la relecture.</p>`;
    return;
  }
  if (!report) {
    panel.innerHTML = `<p class="empty">
      Aucune vérification par recherche web n'a encore été lancée pour ce
      travail. Le texte relu n'est donc pas encore garanti sur les noms
      propres, rapports, statistiques et références juridiques qu'il cite.
    </p>`;
    return;
  }

  const entete = `
    <div class="verify-summary">
      <span class="badge">${report.claims_checked || 0} affirmation${report.claims_checked > 1 ? "s" : ""} vérifiée${report.claims_checked > 1 ? "s" : ""}</span>
      <span class="badge">${report.corrections || 0} correction${report.corrections > 1 ? "s" : ""} appliquée${report.corrections > 1 ? "s" : ""}</span>
      ${enAttente.length ? `<span class="badge warn">${enAttente.length} en attente de validation</span>` : ""}
      ${points.length
        ? `<span class="badge error">${points.length} point${points.length > 1 ? "s" : ""} non confirmé${points.length > 1 ? "s" : ""}</span>`
        : `<span class="verify-ok">Tout ce qui a été vérifié est confirmé</span>`}
    </div>`;

  const pendingSection = enAttente.length ? `
    <h3>À valider</h3>
    <p class="meta">
      Ces corrections ne sont pas assez sûres, ou touchent une catégorie
      sensible, pour être appliquées seules : elles attendent votre décision.
    </p>
    ${enAttente.map(renderPendingCard).join("")}` : "";

  if (!points.length && !enAttente.length) {
    panel.innerHTML = `${entete}
      <p class="meta">
        Les notes de bas de page du texte relu détaillent les sources. Une
        vérification automatique reste une aide, pas une garantie.
      </p>`;
    return;
  }

  const pointsSection = points.length ? `
    <p class="meta">
      Le texte relu porte un appel de note (<code>[^v…]</code>) à chacun de
      ces points ; les sources consultées y sont citées.
    </p>` + points.map(renderFindingCard).join("") : "";

  panel.innerHTML = entete + pendingSection + pointsSection;
}

// Les blocs de révision deviennent la sortie éditoriale canonique dès la
// première modification humaine (docs/PLAN.md, phase 3) — comparaison
// purement textuelle avec le segment brut de même index, sans flag serveur
// dédié : review_blocks_from_segments() garantit la correspondance 1:1.
function isBlockEdited(block, index, segments) {
  const original = segments && segments[index] ? segments[index].text || "" : "";
  return (block.text || "").trim() !== original.trim();
}

function anyBlockEdited() {
  const segments = (state.detail && state.detail.segments) || [];
  return state.blocks.some((block, index) => isBlockEdited(block, index, segments));
}

function currentText() {
  const job = state.detail;
  if (!job) return "";
  if (state.blocks.length && anyBlockEdited()) {
    return state.blocks.map((block) => block.text).join("\n\n");
  }
  return job.clean_text || job.raw_text || "";
}

/* -------------------------------------------------------------- lecteur */

function activeMedia() {
  return state.playerSource === "video" ? $("media-player") : $("audio-player");
}

function loadPlayerForJob(job) {
  if (state.playerJobId === job.id) return;
  state.playerJobId = job.id;
  state.playerSource = "video";

  const video = $("media-player");
  const audio = $("audio-player");
  audio.pause();
  video.pause();
  audio.hidden = true;
  video.hidden = false;
  video.src = `/api/jobs/${job.id}/media`;
  video.load();
  $("player-source-badge").textContent = "Vidéo source";

  video.onerror = () => fallbackToAudio(job);
}

function fallbackToAudio(job) {
  const video = $("media-player");
  const audio = $("audio-player");
  video.onerror = null;
  video.pause();
  video.hidden = true;
  state.playerSource = "audio";
  audio.hidden = false;
  audio.src = `/api/jobs/${job.id}/audio`;
  audio.load();
  $("player-source-badge").textContent = "Audio extrait (WAV)";
}

function playPause() {
  const media = activeMedia();
  if (!media.src) return;
  if (media.paused) media.play().catch(() => {});
  else media.pause();
}

function seekBy(deltaSeconds) {
  const media = activeMedia();
  if (!media.src || !Number.isFinite(media.duration)) return;
  seekTo(media.currentTime + deltaSeconds);
}

function seekTo(seconds) {
  const media = activeMedia();
  if (!media.src) return;
  const duration = Number.isFinite(media.duration) ? media.duration : Infinity;
  media.currentTime = Math.max(0, Math.min(seconds, duration));
}

const SPEEDS = [0.75, 1, 1.25, 1.5, 2];

function setSpeed(rate) {
  state.playerSpeed = rate;
  $("media-player").playbackRate = rate;
  $("audio-player").playbackRate = rate;
  document.querySelectorAll(".speed-btn").forEach((btn) =>
    btn.classList.toggle("is-active", Number(btn.dataset.speed) === rate)
  );
}

function cycleSpeed(direction) {
  const index = SPEEDS.indexOf(state.playerSpeed);
  const next = SPEEDS[Math.min(SPEEDS.length - 1, Math.max(0, index + direction))];
  setSpeed(next);
}

function togglePlayerCollapse() {
  const zone = $("player-zone");
  const collapsed = zone.classList.toggle("is-collapsed");
  $("player-collapse-btn").textContent = collapsed ? "Agrandir" : "Réduire";
  $("player-collapse-btn").setAttribute("aria-expanded", String(!collapsed));
}

function updatePlayPauseIcon() {
  const media = activeMedia();
  $("play-pause-btn").textContent = media.paused ? "▶" : "⏸";
  $("play-pause-btn").setAttribute("aria-label", media.paused ? "Lecture" : "Pause");
}

function updatePlayerTimeDisplay() {
  const media = activeMedia();
  const duration = Number.isFinite(media.duration) ? media.duration : 0;
  $("player-time").textContent = `${clock(media.currentTime)} / ${clock(duration)}`;
  updateTimelineProgress();
  updateActiveBlockFromTime();
}

function initPlayer() {
  ["media-player", "audio-player"].forEach((id) => {
    const media = $(id);
    media.addEventListener("timeupdate", updatePlayerTimeDisplay);
    media.addEventListener("loadedmetadata", updatePlayerTimeDisplay);
    media.addEventListener("play", updatePlayPauseIcon);
    media.addEventListener("pause", updatePlayPauseIcon);
    media.addEventListener("ended", updatePlayPauseIcon);
  });

  $("play-pause-btn").addEventListener("click", playPause);
  $("seek-back-btn").addEventListener("click", () => seekBy(-5));
  $("seek-fwd-btn").addEventListener("click", () => seekBy(5));
  $("player-collapse-btn").addEventListener("click", togglePlayerCollapse);
  document.querySelectorAll(".speed-btn").forEach((btn) =>
    btn.addEventListener("click", () => setSpeed(Number(btn.dataset.speed)))
  );

  initPlayerDrag();
}

function initPlayerDrag() {
  const zone = $("player-zone");
  const handle = $("player-drag-handle");
  let dragging = null;

  handle.addEventListener("pointerdown", (event) => {
    if (window.matchMedia("(max-width: 960px)").matches) return;
    const rect = zone.getBoundingClientRect();
    zone.classList.add("is-floating");
    zone.style.left = `${rect.left}px`;
    zone.style.top = `${rect.top}px`;
    dragging = { offsetX: event.clientX - rect.left, offsetY: event.clientY - rect.top };
    handle.setPointerCapture(event.pointerId);
  });

  handle.addEventListener("pointermove", (event) => {
    if (!dragging) return;
    const maxLeft = window.innerWidth - zone.offsetWidth;
    const maxTop = window.innerHeight - zone.offsetHeight;
    zone.style.left = `${Math.max(0, Math.min(maxLeft, event.clientX - dragging.offsetX))}px`;
    zone.style.top = `${Math.max(0, Math.min(maxTop, event.clientY - dragging.offsetY))}px`;
  });

  const stopDrag = () => { dragging = null; };
  handle.addEventListener("pointerup", stopDrag);
  handle.addEventListener("pointercancel", stopDrag);
  handle.addEventListener("dblclick", () => {
    zone.classList.remove("is-floating");
    zone.style.left = "";
    zone.style.top = "";
  });
}

/* ------------------------------------------------------------ timeline */

function renderTimelineMarkers() {
  const duration = Number($("media-player").duration || $("audio-player").duration) || 0;
  const total = duration || (state.detail && state.detail.duration) || 0;
  const container = $("timeline-markers");
  if (!total || !state.blocks.length) {
    container.innerHTML = "";
    return;
  }
  container.innerHTML = state.blocks
    .map((block) => `<span class="timeline-marker" style="left:${Math.min(100, (block.start / total) * 100)}%"></span>`)
    .join("");
}

function updateTimelineProgress() {
  const media = activeMedia();
  const duration = Number.isFinite(media.duration) && media.duration > 0
    ? media.duration
    : (state.detail && state.detail.duration) || 0;
  const fraction = duration ? Math.min(1, media.currentTime / duration) : 0;
  $("timeline-progress").style.width = `${fraction * 100}%`;
  $("timeline-handle").style.left = `${fraction * 100}%`;
  $("timeline-zone").setAttribute("aria-valuenow", String(Math.round(fraction * duration)));
}

function seekFromTimelineEvent(event) {
  const track = $("timeline-zone").querySelector(".timeline-track");
  const rect = track.getBoundingClientRect();
  const media = activeMedia();
  const duration = Number.isFinite(media.duration) && media.duration > 0
    ? media.duration
    : (state.detail && state.detail.duration) || 0;
  if (!duration) return;
  const fraction = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
  seekTo(fraction * duration);
}

function initTimelineInteraction() {
  const zone = $("timeline-zone");
  let dragging = false;

  zone.addEventListener("pointerdown", (event) => {
    dragging = true;
    zone.setPointerCapture(event.pointerId);
    seekFromTimelineEvent(event);
  });
  zone.addEventListener("pointermove", (event) => {
    if (dragging) seekFromTimelineEvent(event);
  });
  const stop = () => { dragging = false; };
  zone.addEventListener("pointerup", stop);
  zone.addEventListener("pointercancel", stop);

  zone.addEventListener("keydown", (event) => {
    if (event.key === "ArrowLeft") { event.preventDefault(); seekBy(-5); }
    else if (event.key === "ArrowRight") { event.preventDefault(); seekBy(5); }
  });
}

/* ------------------------------------------------------- blocs de révision */

async function loadReviewBlocks(job) {
  // Retenter tant qu'aucun bloc n'a été chargé (transcription en cours à la
  // première sélection) ; une fois chargés, ne pas écraser une édition en
  // cours sur un rafraîchissement SSE pour le même travail.
  if (state.blocksJobId === job.id && state.blocks.length) return;
  try {
    const data = await api(`/api/jobs/${job.id}/review-blocks`);
    state.blocks = data.blocks || [];
    state.blocksJobId = job.id;
  } catch (error) {
    state.blocks = [];
    state.blocksJobId = job.id;
    toast(error.message, true);
  }
  renderBlocks();
  renderTimelineMarkers();
}

function confidenceClass(score) {
  if (score == null || score >= 0.8) return "";
  return score >= 0.6 ? "conf-mid" : "conf-low";
}

function renderBlockText(block, index, segments) {
  const edited = isBlockEdited(block, index, segments);
  const cls = edited ? "" : confidenceClass(block.confidence);
  return `<p class="block-text ${cls}" data-action="edit">${escapeHtml(block.text)}${
    edited ? '<span class="block-edited-badge">modifié</span>' : ""
  }</p>`;
}

function renderBlocks() {
  const list = $("blocks-list");
  const job = state.detail;
  const segments = (job && job.segments) || [];

  if (!state.blocks.length) {
    list.innerHTML = `<p class="empty">Aucun segment.</p>`;
  } else {
    list.innerHTML = state.blocks.map((block, index) => {
      const isEditing = state.editingBlockId === block.id;
      const isActive = state.activeBlockId === block.id;
      const body = isEditing
        ? `<textarea class="block-edit" data-id="${block.id}">${escapeHtml(block.text)}</textarea>
           <div class="block-actions">
             <button type="button" class="btn btn-mini btn-primary" data-action="save" data-id="${block.id}">Enregistrer</button>
             <button type="button" class="btn btn-mini btn-ghost" data-action="cancel" data-id="${block.id}">Annuler</button>
             <span class="block-save-status" id="block-save-status-${block.id}"></span>
           </div>`
        : renderBlockText(block, index, segments);
      return `<div class="block${isActive ? " is-active-block" : ""}" data-block-id="${block.id}">
        <time class="block-time" data-action="seek" data-id="${block.id}">${clock(block.start)}</time>
        <div class="block-body">${body}</div>
      </div>`;
    }).join("");
  }

  $("blocks-edited-flag").hidden = !anyBlockEdited();
}

function seekToBlock(blockId) {
  const block = state.blocks.find((b) => b.id === blockId);
  if (block) seekTo(block.start);
}

function updateActiveBlockFromTime() {
  if (!state.blocks.length) return;
  const time = activeMedia().currentTime;
  let lo = 0, hi = state.blocks.length - 1, found = null;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    const block = state.blocks[mid];
    if (time < block.start) { hi = mid - 1; }
    else if (block.end && time > block.end && mid < state.blocks.length - 1) { lo = mid + 1; }
    else { found = block; break; }
  }
  const nextId = found ? found.id : null;
  if (nextId === state.activeBlockId) return;
  const previous = state.activeBlockId && document.querySelector(`.block[data-block-id="${state.activeBlockId}"]`);
  if (previous) previous.classList.remove("is-active-block");
  state.activeBlockId = nextId;
  if (nextId) {
    const current = document.querySelector(`.block[data-block-id="${nextId}"]`);
    if (current) {
      current.classList.add("is-active-block");
      current.scrollIntoView({ block: "nearest" });
    }
  }
}

function startBlockEdit(blockId) {
  const block = state.blocks.find((b) => b.id === blockId);
  if (!block) return;
  state.editingBlockId = blockId;
  state.editingOriginalText = block.text;
  renderBlocks();
  const textarea = document.querySelector(`.block-edit[data-id="${blockId}"]`);
  if (textarea) { textarea.focus(); textarea.setSelectionRange(textarea.value.length, textarea.value.length); }
}

function cancelBlockEdit() {
  if (!state.editingBlockId) return;
  const block = state.blocks.find((b) => b.id === state.editingBlockId);
  if (block) block.text = state.editingOriginalText;
  state.editingBlockId = null;
  renderBlocks();
}

async function saveBlockEdit(blockId) {
  const textarea = document.querySelector(`.block-edit[data-id="${blockId}"]`);
  const job = state.detail;
  if (!textarea || !job) return;
  const text = textarea.value.trim();
  if (!text) { toast("Le texte du bloc est obligatoire.", true); return; }

  const status = $(`block-save-status-${blockId}`);
  if (status) { status.textContent = "Enregistrement…"; status.className = "block-save-status is-saving"; }

  const block = state.blocks.find((b) => b.id === blockId);
  const previousText = block ? block.text : "";
  if (block) block.text = text;

  try {
    const updated = await api(`/api/jobs/${job.id}/review-blocks/${blockId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    if (block) block.text = updated.text;
    state.editingBlockId = null;
    renderBlocks();
    const savedStatus = $(`block-save-status-${blockId}`);
    if (savedStatus) {
      savedStatus.textContent = "Enregistré.";
      savedStatus.className = "block-save-status is-saved";
    }
  } catch (error) {
    if (block) block.text = previousText;
    if (status) { status.textContent = error.message; status.className = "block-save-status is-error"; }
    toast(error.message, true);
  }
}

function initBlocksList() {
  $("blocks-list").addEventListener("click", (event) => {
    const seekTarget = event.target.closest('[data-action="seek"]');
    if (seekTarget) { seekToBlock(seekTarget.dataset.id); return; }

    const editTarget = event.target.closest('[data-action="edit"]');
    if (editTarget) {
      const blockEl = editTarget.closest(".block");
      if (blockEl) startBlockEdit(blockEl.dataset.blockId);
      return;
    }

    const saveTarget = event.target.closest('[data-action="save"]');
    if (saveTarget) { saveBlockEdit(saveTarget.dataset.id); return; }

    const cancelTarget = event.target.closest('[data-action="cancel"]');
    if (cancelTarget) { cancelBlockEdit(); return; }
  });
}

/* --------------------------------------------------------- comparaison IA */

function renderComparisonPanel(job) {
  const zone = $("comparison-zone");
  if (!job.clean_text) { zone.hidden = true; return; }
  zone.hidden = false;
  $("comparison-body").innerHTML = renderTranscript(job.clean_text);
}

/* --------------------------------------------------------- raccourcis clavier */

function isEditableTarget(target) {
  return Boolean(target.closest && target.closest("input, textarea, select, [contenteditable]"));
}

function initKeyboardShortcuts() {
  document.addEventListener("keydown", (event) => {
    if (state.editingBlockId) {
      if (event.key === "Escape") { event.preventDefault(); cancelBlockEdit(); }
      else if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
        event.preventDefault();
        saveBlockEdit(state.editingBlockId);
      }
      return;
    }

    if (isEditableTarget(event.target)) return;

    if (event.key === " ") { event.preventDefault(); playPause(); }
    else if (event.key === "ArrowLeft") { event.preventDefault(); seekBy(-5); }
    else if (event.key === "ArrowRight") { event.preventDefault(); seekBy(5); }
    else if (event.key === "[") { event.preventDefault(); cycleSpeed(-1); }
    else if (event.key === "]") { event.preventDefault(); cycleSpeed(1); }
    else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "f") {
      const search = $("editor-search-input");
      if (search) {
        event.preventDefault();
        search.disabled = false;
        search.focus();
        search.select();
      }
    }
  });
}

/* ----------------------------------------------------------- réglages */

/* Présentations toutes faites pour l'organisation du coffre Obsidian : évite
   de faire taper des chemins à quelqu'un qui n'a aucune raison de savoir ce
   qu'est un chemin. « Personnalisé » reste disponible pour qui veut choisir. */
const OBSIDIAN_LAYOUTS = {
  formation: {
    notes: "Formation/Transcriptions",
    entities: "Formation/MJPM/Entités",
    index: "Formation/MJPM/MOC Formation.md",
    glossary: "Formation/MJPM/Glossaire MJPM.md",
  },
  racine: {
    notes: "Transcriptions",
    entities: "Entités",
    index: "MOC Formation.md",
    glossary: "Glossaire MJPM.md",
  },
};

const OBSIDIAN_FILENAME_STYLES = {
  "date-titre": "{date} — {titre}",
  "titre-date": "{titre} — {date}",
  "titre": "{titre}",
};

function detectObsidianLayout(settings) {
  for (const [key, layout] of Object.entries(OBSIDIAN_LAYOUTS)) {
    if (
      settings.obsidian_notes_folder === layout.notes &&
      settings.obsidian_entities_folder === layout.entities &&
      settings.obsidian_index_note === layout.index &&
      settings.obsidian_glossary_note === layout.glossary
    ) {
      return key;
    }
  }
  return "custom";
}

function detectFilenameStyle(template) {
  for (const [key, value] of Object.entries(OBSIDIAN_FILENAME_STYLES)) {
    if (template === value) return key;
  }
  return "custom";
}

function updateObsidianLayoutUI() {
  const key = $("obsidian_layout").value;
  $("obsidian_custom_fields").hidden = key !== "custom";
  const layout = OBSIDIAN_LAYOUTS[key];
  if (layout) {
    $("obsidian_notes_folder").value = layout.notes;
    $("obsidian_entities_folder").value = layout.entities;
    $("obsidian_index_note").value = layout.index;
    $("obsidian_glossary_note").value = layout.glossary;
  }
}

function updateObsidianFilenameUI() {
  const key = $("obsidian_filename_style").value;
  $("obsidian_filename_custom_field").hidden = key !== "custom";
  if (OBSIDIAN_FILENAME_STYLES[key]) {
    $("obsidian_filename_template").value = OBSIDIAN_FILENAME_STYLES[key];
  }
}

/* ---------------------------------------------------- navigateur de dossiers */

async function loadFolderBrowser(path) {
  const query = path ? `?path=${encodeURIComponent(path)}` : "";
  let data;
  try {
    data = await api(`/api/browse${query}`);
  } catch (error) {
    toast(error.message, true);
    return;
  }
  renderFolderBrowser(data);
}

function renderFolderBrowser(data) {
  const panel = $("folder-browser");
  panel.dataset.path = data.path;
  $("folder-browser-path").textContent = data.path;

  const rows = [];
  if (data.parent) {
    rows.push(
      `<li class="folder-item" data-path="${escapeHtml(data.parent)}">` +
      `<span class="icon">↰</span> Dossier parent</li>`
    );
  }
  if (data.directories.length) {
    rows.push(...data.directories.map((dir) =>
      `<li class="folder-item" data-path="${escapeHtml(dir.path)}">` +
      `<span class="icon">📁</span> ${escapeHtml(dir.name)}</li>`
    ));
  } else {
    rows.push(`<li class="folder-empty">Aucun sous-dossier ici.</li>`);
  }

  const list = $("folder-browser-list");
  list.innerHTML = rows.join("");
  list.querySelectorAll(".folder-item[data-path]").forEach((item) =>
    item.addEventListener("click", () => loadFolderBrowser(item.dataset.path))
  );
}

function openFolderBrowser() {
  $("folder-browser").hidden = false;
  loadFolderBrowser($("obsidian_vault_path").value.trim() || null);
}

function chooseFolderBrowserPath() {
  const path = $("folder-browser").dataset.path;
  if (path) $("obsidian_vault_path").value = path;
  $("folder-browser").hidden = true;
}

function openSettings() {
  const settings = state.settings;
  $("claude_backend").value = settings.claude_backend || "cli";
  $("claude_cli_path").value = settings.claude_cli_path || "";
  $("proofread_model").value = settings.proofread_model || "";
  $("proofread_effort").value = settings.proofread_effort || "high";
  $("runpod_endpoint_id").value = settings.runpod_endpoint_id || "";
  $("runpod_chunk_seconds").value = settings.runpod_chunk_seconds || 180;
  $("runpod_pod_mode").value = settings.runpod_pod_mode || "off";
  $("runpod_pod_image").value = settings.runpod_pod_image || "";
  $("runpod_pod_gpu_type_id").value = settings.runpod_pod_gpu_type_id || "NVIDIA L4";
  $("runpod_pod_network_volume_id").value = settings.runpod_pod_network_volume_id || "";
  $("keep_media").checked = Boolean(settings.keep_media);
  $("anthropic_api_key").value = "";
  $("runpod_api_key").value = "";
  $("anthropic-state").textContent = settings.anthropic_api_key_set
    ? "Une clé est enregistrée. Laissez vide pour la conserver."
    : "Aucune clé enregistrée.";
  $("runpod-state").textContent = settings.runpod_api_key_set
    ? "Une clé est enregistrée. Laissez vide pour la conserver."
    : "Aucune clé enregistrée.";
  $("claude-state").textContent = settings.claude_backend === "cli"
    ? "Lancez « claude setup-token » une fois, sur cette machine, pour connecter l'abonnement."
    : "Facturé à l'usage sur le compte associé à la clé, indépendamment d'un abonnement Claude.";

  $("factcheck_setting").checked = Boolean(settings.factcheck);
  $("factcheck_max_searches").value = settings.factcheck_max_searches || 8;
  $("lexicon_enabled").checked = Boolean(settings.lexicon_enabled);
  $("lexicon_whisper_prompt").checked = Boolean(settings.lexicon_whisper_prompt);

  $("folder-browser").hidden = true;
  $("obsidian_vault_path").value = settings.obsidian_vault_path || "";
  $("obsidian_notes_folder").value = settings.obsidian_notes_folder || "";
  $("obsidian_entities_folder").value = settings.obsidian_entities_folder || "";
  $("obsidian_index_note").value = settings.obsidian_index_note || "";
  $("obsidian_glossary_note").value = settings.obsidian_glossary_note || "";
  $("obsidian_tags").value = settings.obsidian_tags || "";
  $("obsidian_filename_template").value = settings.obsidian_filename_template || "";
  $("obsidian_create_entities").checked = Boolean(settings.obsidian_create_entities);
  $("obsidian-state").textContent = settings.obsidian_vault_path
    ? ""
    : "Sans coffre configuré, l'étape de publication reste inactive — le reste du traitement fonctionne normalement.";

  const layoutKey = detectObsidianLayout(settings);
  $("obsidian_layout").value = layoutKey;
  $("obsidian_custom_fields").hidden = layoutKey !== "custom";

  const filenameKey = detectFilenameStyle(settings.obsidian_filename_template || "");
  $("obsidian_filename_style").value = filenameKey;
  $("obsidian_filename_custom_field").hidden = filenameKey !== "custom";

  $("settings-dialog").showModal();
}

async function saveSettings() {
  const payload = {
    claude_backend: $("claude_backend").value,
    claude_cli_path: $("claude_cli_path").value.trim(),
    proofread_model: $("proofread_model").value.trim(),
    proofread_effort: $("proofread_effort").value,
    runpod_endpoint_id: $("runpod_endpoint_id").value.trim(),
    runpod_chunk_seconds: Number($("runpod_chunk_seconds").value) || 180,
    runpod_pod_mode: $("runpod_pod_mode").value,
    runpod_pod_image: $("runpod_pod_image").value.trim(),
    runpod_pod_gpu_type_id: $("runpod_pod_gpu_type_id").value.trim() || "NVIDIA L4",
    runpod_pod_network_volume_id: $("runpod_pod_network_volume_id").value.trim(),
    keep_media: $("keep_media").checked,
    default_engine: $("engine").value,
    default_model: $("model").value,
    language: $("language").value,
    default_proofread: $("proofread").value,
    structure_output: $("structure").checked,
    factcheck: $("factcheck_setting").checked,
    factcheck_max_searches: Number($("factcheck_max_searches").value) || 8,
    lexicon_enabled: $("lexicon_enabled").checked,
    lexicon_whisper_prompt: $("lexicon_whisper_prompt").checked,
    obsidian_vault_path: $("obsidian_vault_path").value.trim(),
    obsidian_notes_folder: $("obsidian_notes_folder").value.trim(),
    obsidian_entities_folder: $("obsidian_entities_folder").value.trim(),
    obsidian_index_note: $("obsidian_index_note").value.trim(),
    obsidian_glossary_note: $("obsidian_glossary_note").value.trim(),
    obsidian_tags: $("obsidian_tags").value.trim(),
    obsidian_filename_template: $("obsidian_filename_template").value.trim(),
    obsidian_create_entities: $("obsidian_create_entities").checked,
  };
  const anthropic = $("anthropic_api_key").value.trim();
  if (anthropic) payload.anthropic_api_key = anthropic;
  const runpod = $("runpod_api_key").value.trim();
  if (runpod) payload.runpod_api_key = runpod;

  try {
    await api("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    $("settings-dialog").close();
    await loadStatus();
    toast("Réglages enregistrés.");
  } catch (error) {
    toast(error.message, true);
  }
}

/* --------------------------------------------------- panneaux (mobile) */

// Sur grand écran les trois colonnes (bibliothèque / éditeur / analyse)
// sont visibles ensemble ; sur petit écran, un panneau à la fois — voir
// .shell-tabs en CSS, masquée au-delà du point de rupture desktop.
function initShellTabs() {
  const tabs = Array.from(document.querySelectorAll(".shell-tab"));
  const panels = Array.from(document.querySelectorAll("[data-shell-panel]"));

  function activate(name) {
    tabs.forEach((tab) => tab.classList.toggle("is-active", tab.dataset.shell === name));
    panels.forEach((panel) =>
      panel.classList.toggle("is-shell-active", panel.dataset.shellPanel === name)
    );
  }

  tabs.forEach((tab) => tab.addEventListener("click", () => activate(tab.dataset.shell)));
  activate("workspace");
}

/* ------------------------------------------------------------- actions */

function initActions() {
  $("upload-form").addEventListener("submit", (event) => submitFiles(event, false));
  $("one-click-btn").addEventListener("click", () => submitFiles(null, true));
  $("engine").addEventListener("change", updateEngineDetail);

  $("copy-btn").addEventListener("click", async () => {
    const text = currentText();
    if (!text) return toast("Rien à copier.", true);
    try {
      await navigator.clipboard.writeText(text);
      toast("Texte copié.");
    } catch (_) {
      toast("Copie refusée par le navigateur.", true);
    }
  });

  const menu = $("download-menu");
  $("download-btn").addEventListener("click", (event) => {
    event.stopPropagation();
    menu.hidden = !menu.hidden;
  });
  document.addEventListener("click", () => { menu.hidden = true; });
  menu.querySelectorAll("a").forEach((item) =>
    item.addEventListener("click", () => {
      if (!state.detail) return;
      window.location.href = `/api/jobs/${state.detail.id}/download/${item.dataset.fmt}`;
      menu.hidden = true;
    })
  );

  $("proofread-btn").addEventListener("click", async () => {
    const job = state.detail;
    if (!job) return;
    const button = $("proofread-btn");
    button.disabled = true;
    try {
      await api(`/api/jobs/${job.id}/proofread`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          proofread: $("proofread").value,
          structure: $("structure").checked,
          verify: $("verify").checked,
        }),
      });
      toast("Relecture lancée.");
      await refreshJobs();
    } catch (error) {
      toast(error.message, true);
    } finally {
      button.disabled = false;
    }
  });

  $("factcheck-btn").addEventListener("click", async () => {
    const job = state.detail;
    if (!job) return;
    const button = $("factcheck-btn");
    button.disabled = true;
    try {
      await api(`/api/jobs/${job.id}/factcheck`, { method: "POST" });
      toast("Vérification par recherche web lancée.");
      await refreshJobs();
    } catch (error) {
      toast(error.message, true);
    } finally {
      button.disabled = false;
    }
  });

  $("publish-btn").addEventListener("click", async () => {
    const job = state.detail;
    if (!job) return;
    const button = $("publish-btn");
    button.disabled = true;
    try {
      await api(`/api/jobs/${job.id}/publish`, { method: "POST" });
      toast("Publication dans Obsidian lancée.");
      await refreshJobs();
    } catch (error) {
      toast(error.message, true);
    } finally {
      button.disabled = false;
    }
  });

  $("delete-btn").addEventListener("click", async () => {
    const job = state.detail;
    if (!job) return;
    const running = job.status === "running" || job.status === "queued";
    const message = running
      ? "Annuler et supprimer cette transcription en cours ?"
      : `Supprimer définitivement « ${job.title || job.filename} » ?`;
    if (!window.confirm(message)) return;
    try {
      await api(`/api/jobs/${job.id}`, { method: "DELETE" });
      state.selected = null;
      state.detail = null;
      renderDetail();
      await refreshJobs();
      toast("Transcription supprimée.");
    } catch (error) {
      toast(error.message, true);
    }
  });

  let searchTimer;
  $("search").addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => refreshJobs().catch(() => {}), 250);
  });

  $("open-settings").addEventListener("click", openSettings);
  $("settings-save").addEventListener("click", saveSettings);
  $("settings-cancel").addEventListener("click", () => $("settings-dialog").close());

  $("obsidian_layout").addEventListener("change", updateObsidianLayoutUI);
  $("obsidian_filename_style").addEventListener("change", updateObsidianFilenameUI);
  $("browse-vault-btn").addEventListener("click", openFolderBrowser);
  $("folder-browser-choose").addEventListener("click", chooseFolderBrowserPath);
  $("folder-browser-cancel").addEventListener("click", () => { $("folder-browser").hidden = true; });

  document.addEventListener("click", (event) => {
    const button = event.target.closest(".finding-expand");
    if (!button) return;
    const point = findingRegistry[Number(button.dataset.idx)];
    if (point) openPassage(point);
  });
  $("passage-close").addEventListener("click", () => $("passage-dialog").close());

  document.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-pending-action]");
    if (!button) return;
    const card = button.closest(".pending-correction");
    const job = state.detail;
    if (!card || !job) return;
    const correctionId = card.dataset.correctionId;
    const action = button.dataset.pendingAction;
    card.querySelectorAll("button").forEach((b) => { b.disabled = true; });
    try {
      await api(`/api/jobs/${job.id}/corrections/${correctionId}/${action}`, { method: "POST" });
      toast(action === "valider" ? "Correction validée." : "Correction rejetée.");
      await selectJob(job.id, true);
    } catch (error) {
      toast(error.message, true);
      card.querySelectorAll("button").forEach((b) => { b.disabled = false; });
    }
  });
}

/* ------------------------------------------------------------ init */

(async function main() {
  initDropzone();
  initActions();
  initShellTabs();
  initPlayer();
  initTimelineInteraction();
  initBlocksList();
  initKeyboardShortcuts();
  try {
    await loadStatus();
    await refreshJobs();
    listenEvents();
  } catch (error) {
    toast(`Impossible de contacter le serveur : ${error.message}`, true);
  }
})();
