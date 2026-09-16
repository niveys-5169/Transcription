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
  annotations: [],
  annotationsJobId: null,
  editingAnnotationId: null,
  contextMenuBlockId: null,
  contextMenuSelection: null,
  editorMatches: [],
  editorMatchIndex: -1,
  globalResults: [],
  blocksRenderLimit: 250,
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

function tagsForJob(jobId) {
  try { return JSON.parse(localStorage.getItem(`transcription-tags:${jobId}`) || "[]"); }
  catch (_) { return []; }
}

function saveTagsForJob(jobId, tags) {
  localStorage.setItem(`transcription-tags:${jobId}`, JSON.stringify(tags));
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
  const nim = status.proofread.nim;
  const proofSelect = $("proofread");
  proofSelect.options[0].disabled = !claude.available && !nim?.available;
  proofSelect.options[1].disabled = !nim?.available;
  const configuredMode = status.settings.default_proofread;
  proofSelect.value = configuredMode === "nim" && nim?.available
    ? "nim" : (claude.available || nim?.available) ? configuredMode : "basic";
  $("proofread-detail").textContent = claude.available
    ? `${claude.detail}${nim?.available ? " — NVIDIA NIM est aussi disponible." : ""}`
    : `${claude.detail}${nim?.available ? " NVIDIA NIM est disponible pour une relance directe." : " La relecture simple reste disponible."}`;

  $("factcheck").checked = Boolean(status.settings.factcheck) && claude.available;
  $("factcheck").disabled = !claude.available;
  const obsidian = status.obsidian;
  $("publish").checked = obsidian.available;
  $("publish").disabled = !obsidian.available;
  $("one-click-btn").title = obsidian.available
    ? ""
    : `Publication Obsidian désactivée : ${obsidian.detail}`;
  $("notebooklm-btn").title = status.notebooklm?.detail || "Synchronisation NotebookLM non configurée.";

  const pills = [
    pill(status.ffmpeg.available, "Extraction audio", status.ffmpeg.detail),
    pill(status.engines.local.available, "Moteur local", status.engines.local.detail),
    pill(status.engines.runpod.available, "RunPod", status.engines.runpod.detail, true),
    pill(claude.available, "Claude", claude.detail, true),
    pill(obsidian.available, "Obsidian", obsidian.detail, true),
  ];
  $("health").innerHTML = pills.join("");
  checkForUpdate();
}

async function checkForUpdate() {
  try {
    const update = await api("/api/update");
    $("update-btn").hidden = !update.available;
    if (update.available) $("update-btn").dataset.version = update.version || "";
    renderUpdateProgress(update.installation);
  } catch (_) { /* Une mise à jour ne doit jamais bloquer l'application. */ }
}

function renderUpdateProgress(installation) {
  if (!installation || installation.phase === "idle") return;
  const button = $("update-btn");
  if (installation.phase === "failed") {
    button.disabled = false;
    button.textContent = "Réessayer la mise à jour";
    toast(`${installation.message} Consultez update.log.`, true);
    return;
  }
  button.hidden = false;
  button.disabled = true;
  if (installation.phase === "downloading" && installation.total) {
    button.textContent = `Téléchargement… ${Math.round(installation.downloaded / installation.total * 100)} %`;
  } else {
    button.textContent = installation.message || "Mise à jour en cours…";
  }
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
  const status = $("status-filter").value;
  const date = $("date-filter").value;
  const tag = $("tag-filter").value.trim().toLocaleLowerCase();
  const now = Date.now();
  const periods = { today: 1, week: 7, month: 30 };
  const jobs = state.jobs.filter((job) => {
    if (status && job.status !== status) return false;
    if (date) {
      const age = (now - new Date(job.created_at).getTime()) / 86400000;
      if (!Number.isFinite(age) || age > periods[date]) return false;
    }
    return !tag || tagsForJob(job.id).some((item) => item.toLocaleLowerCase().includes(tag));
  });
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
        ${tagsForJob(job.id).length ? `<div class="job-tags job-tags-compact">${tagsForJob(job.id).map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`).join("")}</div>` : ""}
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
    state.annotations = [];
    state.annotationsJobId = null;
    state.blocksRenderLimit = 250;
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
  else if (job.proofread === "nim") parts.push("relu par NVIDIA NIM (repli)");
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
  $("tags-zone").hidden = !job;
  $("publish-zone").hidden = !job;
  findingRegistry = [];
  if (!job) return;

  $("result-title").textContent = job.title || job.filename;
  $("result-meta").textContent = metaLine(job);

  const errorBanner = $("result-error");
  errorBanner.hidden = !job.error;
  errorBanner.textContent = job.error || "";

  $("cancel-btn").hidden = !["queued", "running"].includes(job.status);
  $("retry-btn").hidden = !["error", "canceled"].includes(job.status);

  const summary = Array.isArray(job.summary) ? job.summary : [];
  $("result-summary").hidden = summary.length === 0;
  $("summary-list").innerHTML = summary.map((point) => `<li>${escapeHtml(point)}</li>`).join("");

  loadPlayerForJob(job);
  renderWaveform(job);
  loadReviewBlocks(job);
  loadAnnotations(job);
  renderComparisonPanel(job);
  $("blocks-zone").hidden = false;

  renderVerification(job);
  renderSources(job);
  renderRevision(job);

  // Chaque étape est à part : on peut la lancer, ou la relancer avec
  // d'autres réglages, sur n'importe quel travail déjà à l'étape d'avant.
  const proofreadBtn = $("proofread-btn");
  proofreadBtn.hidden = !isReadable(job);
  proofreadBtn.textContent = isProofread(job) ? "Relancer la relecture" : "Relire";

  // Après un incident, deux relances directes évitent de devoir retourner
  // dans les réglages ou de refaire l'audio. Elles repartent des segments.
  const retryClaudeBtn = $("retry-claude-btn");
  const retryNimBtn = $("retry-nim-btn");
  const canRetryVerification = Boolean(job.error) && isReadable(job);
  retryClaudeBtn.hidden = !canRetryVerification;
  retryNimBtn.hidden = !canRetryVerification;

  const factcheckBtn = $("factcheck-btn");
  factcheckBtn.hidden = !isProofread(job);
  factcheckBtn.textContent = job.status === "checked" || job.status === "published" ? "Revérifier" : "Vérifier";

  const publishBtn = $("publish-btn");
  publishBtn.hidden = !isProofread(job);
  publishBtn.textContent = job.status === "published" ? "Republier dans Obsidian" : "Publier dans Obsidian";
  [proofreadBtn, retryClaudeBtn, retryNimBtn, factcheckBtn, publishBtn].forEach((button) => button.classList.remove("btn-primary"));
  [proofreadBtn, retryClaudeBtn, retryNimBtn, factcheckBtn, publishBtn].forEach((button) => button.classList.add("btn-ghost"));
  const mainAction = !isProofread(job) ? proofreadBtn
    : job.status === "done" ? factcheckBtn
    : job.status === "published" ? $("notebooklm-btn") : publishBtn;
  if (!mainAction.hidden) { mainAction.classList.remove("btn-ghost"); mainAction.classList.add("btn-primary"); }

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

  const notebookButton = $("notebooklm-btn");
  const courseDocLink = $("notebooklm-course-link");
  if (job.notebooklm_doc_id) {
    courseDocLink.href = `https://docs.google.com/document/d/${encodeURIComponent(job.notebooklm_doc_id)}/edit`;
    courseDocLink.hidden = false;
  } else {
    courseDocLink.hidden = true;
  }
  const notebookReady = Boolean(
    state.settings.notebooklm_sync_enabled && state.settings.notebooklm_master_doc_id
  );
  const published = Boolean(job.obsidian_path);
  notebookButton.disabled = !published || !notebookReady || job.notebooklm_status === "en_cours";
  notebookButton.title = !published
    ? "Disponible après une publication Obsidian réussie."
    : !notebookReady
      ? "Configurez le Doc maître Google dans les réglages."
      : job.notebooklm_status === "en_cours"
        ? "Synchronisation en cours…"
        : "Met à jour le même Doc maître Google.";
  const syncLabels = {
    synchronise: "NotebookLM synchronisé",
    en_cours: "Synchronisation NotebookLM en cours…",
    erreur: job.notebooklm_error || "Synchronisation NotebookLM en erreur.",
  };
  $("publication-status").textContent = job.obsidian_path
    ? `Obsidian publié${job.obsidian_published_at ? ` le ${humanDate(job.obsidian_published_at)}` : ""} · ${syncLabels[job.notebooklm_status] || (notebookReady ? "À synchroniser avec NotebookLM" : "NotebookLM non configuré")}`
    : "Obsidian : prêt à publier.";

  renderJobTags(job);

}

function renderRevision(job) {
  const zone = $("revision-zone");
  const panel = $("panel-revision");
  const button = $("revision-btn");
  const revision = job.revision;
  zone.hidden = !isProofread(job);
  button.disabled = job.task === "revision";
  button.textContent = revision ? "Régénérer la fiche" : "Générer la fiche";
  if (!revision) { panel.innerHTML = ""; return; }
  const points = Array.isArray(revision.points_cles) ? revision.points_cles : [];
  const questions = Array.isArray(revision.questions) ? revision.questions : [];
  panel.innerHTML = `${points.length ? `<h4>Points clés</h4><ul>${points.map((point) => `<li>${escapeHtml(point)}</li>`).join("")}</ul>` : ""}${questions.length ? `<h4>Questions</h4>${questions.slice(0, 5).map((item) => `<details><summary>${escapeHtml(item.q || "Question")}</summary><p>${escapeHtml(item.r || "")}</p></details>`).join("")}` : ""}`;
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
        <time${point.block_id ? ` class="block-time" data-action="focus-finding" data-block-id="${escapeHtml(point.block_id)}"` : ""}>${clock(point.start)}</time>
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

/* ------------------------------------------------------- annotations */

const ANNOTATION_STATUS_LABELS = {
  a_verifier: "À vérifier", valide: "Validé", ignore: "Ignoré",
};

async function loadAnnotations(job) {
  if (state.annotationsJobId === job.id) return;
  try {
    const data = await api(`/api/jobs/${job.id}/annotations`);
    state.annotations = data.annotations || [];
    state.annotationsJobId = job.id;
  } catch (error) {
    state.annotations = [];
    state.annotationsJobId = job.id;
    toast(error.message, true);
  }
  renderAnnotations();
  renderTimelineMarkers();
}

function renderAnnotations() {
  const filter = $("annotation-filter").value;
  const annotations = state.annotations.filter((item) => !filter || item.status === filter);
  const list = $("annotation-list");
  if (!annotations.length) {
    list.innerHTML = `<p class="empty">Aucun élément${filter ? " dans cet état" : ""}.</p>`;
    return;
  }
  list.innerHTML = annotations.map((item) => {
    const block = state.blocks.find((candidate) => candidate.id === item.block_id);
    const label = block ? clock(block.start) : "Bloc supprimé";
    const color = item.color ? ` annotation-highlight-${escapeHtml(item.color)}` : "";
    const isEditing = state.editingAnnotationId === item.id;
    const canEditContent = item.type !== "highlight";
    const content = isEditing
      ? `<textarea class="annotation-edit" data-annotation-edit-id="${item.id}">${escapeHtml(item.content || "")}</textarea>`
      : item.content ? `<p>${escapeHtml(item.content)}</p>` : "";
    const needsReview = item.status === "a_verifier";
    const quickActions = needsReview && !isEditing
      ? `<div class="annotation-actions annotation-actions-top">
          <button type="button" class="btn btn-mini btn-primary" data-annotation-action="valider">Valider</button>
          ${canEditContent ? `<button type="button" class="btn btn-mini btn-ghost" data-annotation-action="edit">Modifier</button>` : ""}
        </div>` : "";
    const editActions = isEditing
      ? `<div class="annotation-actions annotation-actions-top">
          <button type="button" class="btn btn-mini btn-primary" data-annotation-action="save-edit">Enregistrer</button>
          <button type="button" class="btn btn-mini btn-ghost" data-annotation-action="cancel-edit">Annuler</button>
        </div>` : "";
    return `<article class="annotation ${escapeHtml(item.type)}${color}" data-annotation-id="${item.id}">
      <div class="annotation-head">
        <button type="button" class="annotation-jump" data-annotation-action="jump" data-block-id="${escapeHtml(item.block_id)}">${label}</button>
        <span class="badge">${escapeHtml(item.type === "review" ? "révision" : item.type === "highlight" ? "surlignage" : "note")}</span>
      </div>
      ${quickActions}
      ${editActions}
      ${content}
      <div class="annotation-actions">
        <select data-annotation-action="status" aria-label="État de l’annotation">
          ${Object.entries(ANNOTATION_STATUS_LABELS).map(([value, text]) => `<option value="${value}"${item.status === value ? " selected" : ""}>${text}</option>`).join("")}
        </select>
        <button type="button" class="btn btn-mini btn-danger-ghost" data-annotation-action="delete">Supprimer</button>
      </div>
    </article>`;
  }).join("");
}

async function createAnnotation(blockId, kind, extra = {}) {
  const job = state.detail;
  if (!job || !blockId) return;
  try {
    const item = await api(`/api/jobs/${job.id}/annotations`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ block_id: blockId, type: kind, ...extra }),
    });
    state.annotations.push(item);
    renderAnnotations();
    renderTimelineMarkers();
    refreshJobs().catch(() => {});
  } catch (error) { toast(error.message, true); }
}

async function updateAnnotation(annotationId, fields) {
  const job = state.detail;
  if (!job) return;
  try {
    const updated = await api(`/api/jobs/${job.id}/annotations/${annotationId}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(fields),
    });
    state.annotations = state.annotations.map((item) => item.id === annotationId ? updated : item);
    renderAnnotations();
    renderTimelineMarkers();
    refreshJobs().catch(() => {});
  } catch (error) { toast(error.message, true); }
}

async function removeAnnotation(annotationId) {
  const job = state.detail;
  if (!job) return;
  try {
    await api(`/api/jobs/${job.id}/annotations/${annotationId}`, { method: "DELETE" });
    state.annotations = state.annotations.filter((item) => item.id !== annotationId);
    renderAnnotations();
    renderTimelineMarkers();
    refreshJobs().catch(() => {});
  } catch (error) { toast(error.message, true); }
}

function renderJobTags(job) {
  const tags = tagsForJob(job.id);
  $("job-tags").innerHTML = tags.length
    ? tags.map((tag) => `<button type="button" class="tag tag-remove" data-tag="${escapeHtml(tag)}" title="Retirer ${escapeHtml(tag)}">${escapeHtml(tag)} ×</button>`).join("")
    : `<p class="meta">Visibles uniquement dans ce navigateur.</p>`;
}

function addJobTag() {
  const job = state.detail;
  const input = $("job-tag-input");
  const tag = input.value.trim().replace(/\s+/g, " ");
  if (!job || !tag) return;
  const tags = tagsForJob(job.id);
  if (!tags.some((item) => item.toLocaleLowerCase() === tag.toLocaleLowerCase())) {
    saveTagsForJob(job.id, [...tags, tag]);
  }
  input.value = "";
  renderJobTags(job);
  renderJobs();
}

/* ------------------------------------------------ recherche dans l’éditeur */

function editorQuery() { return $("editor-search-input").value.trim(); }

function updateEditorMatches({ preserve = false } = {}) {
  const query = editorQuery().toLocaleLowerCase();
  const previousId = preserve && state.editorMatches[state.editorMatchIndex];
  state.editorMatches = query ? state.blocks.filter((block) => block.text.toLocaleLowerCase().includes(query)).map((block) => block.id) : [];
  state.editorMatchIndex = previousId ? state.editorMatches.indexOf(previousId) : -1;
  if (state.editorMatchIndex < 0 && state.editorMatches.length) state.editorMatchIndex = 0;
  $("editor-search-count").textContent = state.editorMatches.length
    ? `${state.editorMatchIndex + 1}/${state.editorMatches.length}` : (query ? "0 résultat" : "");
}

function highlightEditorText(text) {
  const query = editorQuery();
  if (!query) return escapeHtml(text);
  const expression = new RegExp(`(${query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})`, "gi");
  return escapeHtml(text).replace(expression, "<mark class=\"editor-match\">$1</mark>");
}

function moveEditorMatch(direction) {
  if (!state.editorMatches.length) return;
  state.editorMatchIndex = (state.editorMatchIndex + direction + state.editorMatches.length) % state.editorMatches.length;
  const blockId = state.editorMatches[state.editorMatchIndex];
  updateEditorMatches({ preserve: true });
  const node = document.querySelector(`.block[data-block-id="${blockId}"]`);
  if (node) node.scrollIntoView({ block: "center", behavior: "smooth" });
  seekToBlock(blockId);
}

// Les blocs de révision deviennent la sortie éditoriale canonique dès la
// première modification humaine (docs/PLAN.md, phase 3) — comparaison
// purement textuelle avec le segment brut de même index, sans flag serveur
// dédié : review_blocks_from_segments() garantit la correspondance 1:1.
function isBlockEdited(block, index, segments) {
  const original = block.raw_text || (segments && segments[index] ? segments[index].text || "" : "");
  return (block.text || "").trim() !== original.trim();
}

function anyBlockEdited() {
  const segments = (state.detail && state.detail.segments) || [];
  return state.blocks.some((block, index) => isBlockEdited(block, index, segments));
}

function currentText() {
  const job = state.detail;
  if (!job) return "";
  if (state.blocks.length) return state.blocks.map((block) => block.text).join("\n\n");
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
  $("player-unavailable").hidden = true;
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
  audio.onerror = () => {
    audio.onerror = null;
    audio.pause();
    audio.hidden = true;
    $("player-unavailable").hidden = false;
    $("player-source-badge").textContent = "Média indisponible";
  };
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
  updateActiveWordFromTime();
}

function updateActiveWordFromTime() {
  const previous = document.querySelector(".word.is-playing");
  const block = state.blocks.find((item) => item.id === state.activeBlockId);
  const time = activeMedia().currentTime;
  const word = block && (block.words || []).find((item) => time >= Number(item.start) && time <= Number(item.end));
  const next = word && document.querySelector(`.word[data-time="${Number(word.start)}"]`);
  if (previous === next) return;
  if (previous) previous.classList.remove("is-playing");
  if (next) next.classList.add("is-playing");
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
  const reviewBlockIds = new Set(state.annotations
    .filter((item) => item.type === "review" && item.status === "a_verifier")
    .map((item) => item.block_id));
  container.innerHTML = state.blocks
    .map((block) => `<span class="timeline-marker ${confidenceClass(block.confidence)}${reviewBlockIds.has(block.id) ? " needs-review" : ""}" title="${markerLabel(block, reviewBlockIds.has(block.id))}" style="left:${Math.min(100, (block.start / total) * 100)}%"></span>`)
    .join("");
}

function markerLabel(block, needsReview) {
  if (needsReview) return `À vérifier — ${clock(block.start)}`;
  if (block.confidence != null && block.confidence < 0.6) return `Confiance faible (${Math.round(block.confidence * 100)} %) — ${clock(block.start)}`;
  if (block.confidence != null && block.confidence < 0.8) return `Confiance moyenne (${Math.round(block.confidence * 100)} %) — ${clock(block.start)}`;
  return `Confiance élevée — ${clock(block.start)}`;
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
    state.blocksRenderLimit = 250;
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
  if (!edited && Array.isArray(block.words) && block.words.length) {
    const words = block.words.map((word) => `<span class="word ${confidenceClass(word.confidence)}" data-action="seek-word" data-time="${Number(word.start)}" title="${clock(word.start)}">${escapeHtml(word.text)}</span>`).join("");
    return `<p class="block-text ${cls}" data-action="edit">${words}</p>`;
  }
  const highlights = state.annotations.filter((item) => item.type === "highlight" && item.block_id === block.id && Number.isInteger(item.range_start) && Number.isInteger(item.range_end))
    .sort((a, b) => a.range_start - b.range_start);
  let cursor = 0;
  const rendered = highlights.reduce((html, item) => {
    if (item.range_start < cursor || item.range_end > block.text.length) return html;
    cursor = item.range_end;
    return html + escapeHtml(block.text.slice(cursor === item.range_end ? 0 : 0, 0));
  }, "");
  // Une seule plage active est rendue par annotation ; les plages qui se
  // chevauchent restent listées mais ne cassent jamais le texte éditable.
  let html = "", position = 0;
  for (const item of highlights) {
    if (item.range_start < position || item.range_end > block.text.length) continue;
    html += escapeHtml(block.text.slice(position, item.range_start));
    html += `<mark class="annotation-highlight-${escapeHtml(item.color || "yellow")}">${escapeHtml(block.text.slice(item.range_start, item.range_end))}</mark>`;
    position = item.range_end;
  }
  html += highlights.length ? escapeHtml(block.text.slice(position)) : highlightEditorText(block.text);
  return `<p class="block-text ${cls}" data-action="edit">${html}${
    edited ? '<span class="block-edited-badge">modifié</span>' : ""
  }</p>`;
}

function renderSpeakerControls(block) {
  const role = block.role || "";
  return `<div class="speaker-controls" aria-label="Identifier l'intervenant">
    <select class="speaker-role" data-action="role" data-id="${block.id}" aria-label="Rôle de l'intervenant">
      <option value=""${role ? "" : " selected"}>Qui parle ?</option>
      <option value="professeur"${role === "professeur" ? " selected" : ""}>Professeur</option>
      <option value="eleve"${role === "eleve" ? " selected" : ""}>Élève</option>
      <option value="intervenant"${role === "intervenant" ? " selected" : ""}>Autre intervenant</option>
    </select>
    <input class="speaker-name" data-action="speaker" data-id="${block.id}" value="${escapeHtml(block.speaker || "")}" placeholder="Nom ou repère (facultatif)" aria-label="Nom ou repère de l'intervenant">
  </div>`;
}

function speakerColorClass(speaker) {
  if (!speaker) return "speaker-color-0";
  let hash = 0;
  for (const char of speaker) hash = ((hash << 5) - hash + char.charCodeAt(0)) | 0;
  return `speaker-color-${Math.abs(hash) % 6}`;
}

function renderBlocks() {
  const list = $("blocks-list");
  const job = state.detail;
  const segments = (job && job.segments) || [];
  updateEditorMatches({ preserve: true });

  if (!state.blocks.length) {
    list.innerHTML = `<p class="empty">Aucun segment.</p>`;
  } else {
    const visible = state.blocks.slice(0, state.blocksRenderLimit);
    const reviewBlockIds = new Set(state.annotations
      .filter((item) => item.type === "review" && item.status === "a_verifier")
      .map((item) => item.block_id));
    list.innerHTML = visible.map((block, index) => {
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
      const confidence = confidenceClass(block.confidence);
      const needsReview = reviewBlockIds.has(block.id);
      const speakerControls = renderSpeakerControls(block);
      const confidenceLabel = block.confidence == null ? "" : `<span class="confidence-badge ${confidence}">${Math.round(block.confidence * 100)} % ${block.confidence < .6 ? "— confiance faible" : block.confidence < .8 ? "— à confirmer" : "— confiance élevée"}</span>`;
      const raw = block.raw_text || (block.source_segment_ids || []).map((id) => {
        const at = Number(id.replace("segment-", "")) - 1; return (segments[at] || {}).text || "";
      }).join(" ");
      const hasReviewed = job && ["done", "checked", "published"].includes(job.status);
      const rawControl = hasReviewed ? `<details class="block-raw"><summary>Brut</summary><p>${escapeHtml(raw)}</p><button type="button" class="btn btn-mini btn-ghost" data-action="seek" data-id="${block.id}">Écouter ce passage</button></details>` : "";
      const previous = visible[index - 1];
      const groupHeader = block.speaker && (!previous || previous.speaker !== block.speaker)
        ? `<div class="speaker-turn ${speakerColorClass(block.speaker)}"><span class="speaker-dot"></span><b>${escapeHtml(block.speaker)}</b><span>tour de parole</span></div>` : "";
      return `${groupHeader}<div class="block ${speakerColorClass(block.speaker)} ${confidence}${needsReview ? " needs-review" : ""}${isActive ? " is-active-block" : ""}" data-block-id="${block.id}">
        <time class="block-time${needsReview ? " needs-review" : ""}" data-action="seek" data-id="${block.id}" title="${needsReview ? "Passage à vérifier" : "Aller à cet horodatage"}">${clock(block.start)}</time>
        <div class="block-body">${speakerControls}${body}${confidenceLabel}${rawControl}</div>
      </div>`;
    }).join("");
  }

  const more = $("blocks-more-btn");
  const remaining = state.blocks.length - state.blocksRenderLimit;
  more.hidden = remaining <= 0;
  if (remaining > 0) more.textContent = `Afficher les ${Math.min(250, remaining)} blocs suivants (${remaining} restants)`;

  const title = document.querySelector(".blocks-header h3");
  if (title) title.textContent = job && ["done", "checked", "published"].includes(job.status) ? "Transcription relue" : "Transcription brute";
  $("blocks-edited-flag").hidden = !anyBlockEdited();
  renderManualReviewStatus();
}

function renderManualReviewStatus() {
  const status = state.detail && state.detail.manual_review_status;
  const labels = { in_progress: "Repasse en cours", completed: "Repasse terminée" };
  $("manual-review-status").textContent = labels[status] || "";
  $("manual-review-btn").textContent = status === "in_progress"
    ? "Terminer la repasse" : status === "completed" ? "Reprendre la repasse" : "Relecture manuelle";
}

async function saveSpeakerField(blockId, fields) {
  const job = state.detail;
  const block = state.blocks.find((item) => item.id === blockId);
  if (!job || !block) return;
  const previous = { speaker: block.speaker, role: block.role };
  // Les blocs qui partagent le même repère (ex. "Speaker 1") sont mis à jour
  // ensemble : renommer le repère ou lui donner un rôle s'applique à tous.
  const groupKey = previous.speaker;
  const grouped = groupKey
    ? state.blocks.filter((item) => item !== block && item.speaker === groupKey)
    : [];
  const groupedPrevious = grouped.map((item) => ({ item, speaker: item.speaker, role: item.role }));
  Object.assign(block, fields);
  grouped.forEach((item) => {
    if (Object.prototype.hasOwnProperty.call(fields, "speaker")) item.speaker = block.speaker;
    if (Object.prototype.hasOwnProperty.call(fields, "role")) item.role = block.role;
  });
  try {
    const updated = await api(`/api/jobs/${job.id}/review-blocks/${blockId}`, {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(fields),
    });
    Object.assign(block, updated);
    renderBlocks();
  } catch (error) {
    Object.assign(block, previous);
    groupedPrevious.forEach(({ item, speaker, role }) => { item.speaker = speaker; item.role = role; });
    renderBlocks();
    toast(error.message, true);
  }
}

function seekToBlock(blockId) {
  const block = state.blocks.find((b) => b.id === blockId);
  if (block) seekTo(block.start);
}

async function renderWaveform(job) {
  const canvas = $("timeline-waveform");
  if (!job || !canvas) return;
  try {
    const { peaks } = await api(`/api/jobs/${job.id}/peaks`);
    const width = Math.max(1, canvas.clientWidth || 600), height = Math.max(1, canvas.clientHeight || 42);
    const ratio = window.devicePixelRatio || 1;
    canvas.width = width * ratio; canvas.height = height * ratio;
    const ctx = canvas.getContext("2d"); ctx.scale(ratio, ratio); ctx.clearRect(0, 0, width, height);
    ctx.strokeStyle = "rgba(174, 160, 255, .55)"; ctx.lineWidth = 1;
    const middle = height / 2;
    peaks.forEach((peak, index) => {
      const x = (index / Math.max(1, peaks.length - 1)) * width;
      const amplitude = Math.max(1, peak * middle);
      ctx.beginPath(); ctx.moveTo(x, middle - amplitude); ctx.lineTo(x, middle + amplitude); ctx.stroke();
    });
  } catch (_) { canvas.hidden = true; }
}

function focusBlock(blockId) {
  const index = state.blocks.findIndex((block) => block.id === blockId);
  if (index < 0) return;
  if (index >= state.blocksRenderLimit) state.blocksRenderLimit = Math.ceil((index + 1) / 250) * 250;
  state.activeBlockId = blockId;
  renderBlocks(); renderAnnotations(); seekToBlock(blockId);
  const node = document.querySelector(`.block[data-block-id="${blockId}"]`);
  if (node) {
    node.scrollIntoView({ block: "center", behavior: "smooth" });
    node.classList.add("block-flash");
    setTimeout(() => node.classList.remove("block-flash"), 1500);
  }
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
  const nextIndex = state.blocks.findIndex((block) => block.id === nextId);
  if (nextIndex >= state.blocksRenderLimit) {
    state.blocksRenderLimit = Math.ceil((nextIndex + 1) / 250) * 250;
    renderBlocks();
  }
  if (nextId) {
    const current = document.querySelector(`.block[data-block-id="${nextId}"]`);
    if (current) {
      current.classList.add("is-active-block");
      current.scrollIntoView({ block: "nearest" });
    }
  }
  renderAnnotations();
}

function startBlockEdit(blockId) {
  const block = state.blocks.find((b) => b.id === blockId);
  if (!block) return;
  state.activeBlockId = blockId;
  state.editingBlockId = blockId;
  state.editingOriginalText = block.text;
  renderBlocks();
  renderAnnotations();
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
    const wordTarget = event.target.closest('[data-action="seek-word"]');
    if (wordTarget) {
      event.stopPropagation();
      seekTo(Number(wordTarget.dataset.time));
      return;
    }
    const seekTarget = event.target.closest('[data-action="seek"]');
    if (seekTarget) {
      state.activeBlockId = seekTarget.dataset.id;
      renderBlocks();
      renderAnnotations();
      seekToBlock(seekTarget.dataset.id);
      return;
    }

    const editTarget = event.target.closest('[data-action="edit"]');
    if (editTarget) {
      if (window.getSelection().toString()) return;
      const blockEl = editTarget.closest(".block");
      if (blockEl) startBlockEdit(blockEl.dataset.blockId);
      return;
    }

    const saveTarget = event.target.closest('[data-action="save"]');
    if (saveTarget) { saveBlockEdit(saveTarget.dataset.id); return; }

    const cancelTarget = event.target.closest('[data-action="cancel"]');
    if (cancelTarget) { cancelBlockEdit(); return; }
  });
  $("blocks-list").addEventListener("change", (event) => {
    const select = event.target.closest('[data-action="role"]');
    if (select) saveSpeakerField(select.dataset.id, { role: select.value || null });
  });
  $("blocks-list").addEventListener("focusout", (event) => {
    const input = event.target.closest('[data-action="speaker"]');
    if (input) saveSpeakerField(input.dataset.id, { speaker: input.value.trim() || null });
  });
  $("blocks-list").addEventListener("contextmenu", (event) => {
    if (event.target.closest(".block-edit")) return; // laisser le menu natif pour l'édition en cours
    const blockEl = event.target.closest(".block");
    if (!blockEl) return;
    event.preventDefault();
    const selection = window.getSelection();
    state.contextMenuSelection = null;
    if (selection && selection.toString() && selection.rangeCount && selection.anchorNode && blockEl.querySelector(".block-text")?.contains(selection.anchorNode)) {
      const textEl = blockEl.querySelector(".block-text");
      const range = selection.getRangeAt(0);
      const before = range.cloneRange(); before.selectNodeContents(textEl); before.setEnd(range.startContainer, range.startOffset);
      state.contextMenuSelection = { blockId: blockEl.dataset.blockId, start: before.toString().length, end: before.toString().length + selection.toString().length };
    }
    openBlockContextMenu(event.clientX, event.clientY, blockEl.dataset.blockId);
  });
}

/* ------------------------------------------- menu contextuel (transcription) */

function openBlockContextMenu(x, y, blockId) {
  const menu = $("block-context-menu");
  state.contextMenuBlockId = blockId;
  state.activeBlockId = blockId;
  renderBlocks();
  menu.querySelector(".context-menu-main").hidden = false;
  const noteView = menu.querySelector(".context-menu-note");
  noteView.hidden = true;
  $("context-menu-note-content").value = "";
  menu.hidden = false;
  // Position dans la fenêtre visible, sans déborder du bord droit/bas.
  const rect = menu.getBoundingClientRect();
  const maxX = window.innerWidth - rect.width - 8;
  const maxY = window.innerHeight - rect.height - 8;
  menu.style.left = `${Math.max(8, Math.min(x, maxX))}px`;
  menu.style.top = `${Math.max(8, Math.min(y, maxY))}px`;
}

function closeBlockContextMenu() {
  const menu = $("block-context-menu");
  if (menu.hidden) return;
  menu.hidden = true;
  state.contextMenuBlockId = null;
}

function showContextMenuNoteEditor() {
  const menu = $("block-context-menu");
  menu.querySelector(".context-menu-main").hidden = true;
  menu.querySelector(".context-menu-note").hidden = false;
  $("context-menu-note-content").focus();
}

function initBlockContextMenu() {
  const menu = $("block-context-menu");
  menu.addEventListener("click", (event) => {
    const action = event.target.dataset.menuAction;
    const highlight = event.target.dataset.menuHighlight;
    const blockId = state.contextMenuBlockId;
    if (!blockId) return;
    if (action === "note") { showContextMenuNoteEditor(); return; }
    if (action === "review") {
      createAnnotation(blockId, "review", { status: "a_verifier" });
      closeBlockContextMenu();
      return;
    }
    if (highlight) {
      const selected = state.contextMenuSelection;
      const extra = { color: highlight, status: "a_verifier" };
      if (selected && selected.blockId === blockId) { extra.range_start = selected.start; extra.range_end = selected.end; }
      createAnnotation(blockId, "highlight", extra);
      closeBlockContextMenu();
      return;
    }
  });
  $("context-menu-note-save").addEventListener("click", () => {
    const blockId = state.contextMenuBlockId;
    const content = $("context-menu-note-content").value.trim();
    if (!blockId) return;
    if (!content) return toast("Écrivez une note avant de l’ajouter.", true);
    createAnnotation(blockId, "note", { content, status: "a_verifier" });
    closeBlockContextMenu();
  });
  $("context-menu-note-cancel").addEventListener("click", closeBlockContextMenu);

  document.addEventListener("click", (event) => {
    if (menu.hidden || menu.contains(event.target)) return;
    closeBlockContextMenu();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeBlockContextMenu();
  });
  window.addEventListener("scroll", closeBlockContextMenu, true);
  window.addEventListener("resize", closeBlockContextMenu);
}

/* -------------------------------------------------- recherche bibliothèque */

function renderGlobalSearchResults() {
  const zone = $("global-search-results");
  const query = $("search").value.trim();
  zone.hidden = !query;
  if (!query) { zone.innerHTML = ""; return; }
  if (!state.globalResults.length) {
    zone.innerHTML = `<p class="empty">Aucun passage trouvé.</p>`;
    return;
  }
  zone.innerHTML = `<p class="global-search-label">Passages trouvés</p>${state.globalResults.map((result) => `
    <button type="button" class="global-search-result" data-job-id="${escapeHtml(result.job_id)}" data-start="${result.start ?? ""}">
      <strong>${escapeHtml(result.title)}</strong>
      <span>${escapeHtml(result.match_text)}</span>
      <small>${result.match_count} occurrence${result.match_count > 1 ? "s" : ""}${result.start != null ? ` · ${clock(result.start)}` : ""}</small>
    </button>`).join("")}`;
}

async function refreshGlobalSearch() {
  const query = $("search").value.trim();
  if (!query) { state.globalResults = []; renderGlobalSearchResults(); return; }
  const data = await api(`/api/search?q=${encodeURIComponent(query)}`);
  // Une réponse plus ancienne ne doit pas remplacer la requête en cours.
  if (query !== $("search").value.trim()) return;
  state.globalResults = data.results || [];
  renderGlobalSearchResults();
}

/* --------------------------------------------------------- comparaison IA */

function renderComparisonPanel(job) {
  const zone = $("comparison-zone");
  // Le relu est désormais l'éditeur central ; le brut est disponible par
  // passage via « Brut », il n'y a plus de seconde version concurrente.
  zone.hidden = true;
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
  $("domain_label").value = settings.domain_label || "MJPM";
}

function openSettings() {
  const settings = state.settings;
  $("claude_backend").value = settings.claude_backend || "cli";
  $("claude_cli_path").value = settings.claude_cli_path || "";
  $("proofread_model").value = settings.proofread_model || "";
  $("proofread_effort").value = settings.proofread_effort || "high";
  $("nim_fallback_enabled").checked = Boolean(settings.nim_fallback_enabled);
  $("nim_model").value = settings.nim_model || "";
  $("nim_base_url").value = settings.nim_base_url || "";
  $("runpod_chunk_seconds").value = settings.runpod_chunk_seconds || 180;
  $("runpod_pod_image").value = settings.runpod_pod_image || "";
  $("runpod_pod_gpu_type_id").value = settings.runpod_pod_gpu_type_id || "NVIDIA L4";
  $("runpod_pod_network_volume_id").value = settings.runpod_pod_network_volume_id || "";
  $("keep_media").checked = Boolean(settings.keep_media);
  $("notebooklm_sync_enabled").checked = Boolean(settings.notebooklm_sync_enabled);
  $("notebooklm_master_doc_enabled").checked = settings.notebooklm_master_doc_enabled !== false;
  $("notebooklm_drive_folder_id").value = settings.notebooklm_drive_folder_id || "";
  $("notebooklm_master_doc_id").value = settings.notebooklm_master_doc_id || "";
  $("notebooklm-settings-state").textContent = settings.notebooklm_master_doc_id
    ? (settings.notebooklm_sync_enabled ? "Prêt : le Doc maître sera mis à jour automatiquement." : "Doc maître trouvé, mais la synchronisation automatique est désactivée.")
    : "À initialiser : aucun Doc maître Google n'est encore associé.";
  $("anthropic_api_key").value = "";
  $("nim_api_key").value = "";
  $("runpod_api_key").value = "";
  $("hf_token").value = "";
  $("anthropic-state").textContent = settings.anthropic_api_key_set
    ? "Une clé est enregistrée. Laissez vide pour la conserver."
    : "Aucune clé enregistrée.";
  $("nim-state").textContent = settings.nim_api_key_set
    ? "Une clé est enregistrée. Laissez vide pour la conserver."
    : "Aucune clé enregistrée.";
  $("runpod-state").textContent = settings.runpod_api_key_set
    ? "Une clé est enregistrée. Laissez vide pour la conserver."
    : "Aucune clé enregistrée.";
  $("hf-token-state").textContent = settings.hf_token_set
    ? "Jeton Hugging Face enregistré. Laissez vide pour le conserver."
    : "Requis pour la diarisation pyannote sur le pod.";
  $("diarization_enabled").checked = Boolean(settings.diarization_enabled);
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
    nim_fallback_enabled: $("nim_fallback_enabled").checked,
    nim_model: $("nim_model").value.trim(),
    nim_base_url: $("nim_base_url").value.trim(),
    runpod_chunk_seconds: Number($("runpod_chunk_seconds").value) || 180,
    runpod_pod_image: $("runpod_pod_image").value.trim(),
    runpod_pod_gpu_type_id: $("runpod_pod_gpu_type_id").value.trim() || "NVIDIA L4",
    runpod_pod_network_volume_id: $("runpod_pod_network_volume_id").value.trim(),
    diarization_enabled: $("diarization_enabled").checked,
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
    domain_label: $("domain_label").value.trim() || "MJPM",
    obsidian_vault_path: $("obsidian_vault_path").value.trim(),
    obsidian_notes_folder: $("obsidian_notes_folder").value.trim(),
    obsidian_entities_folder: $("obsidian_entities_folder").value.trim(),
    obsidian_index_note: $("obsidian_index_note").value.trim(),
    obsidian_glossary_note: $("obsidian_glossary_note").value.trim(),
    obsidian_tags: $("obsidian_tags").value.trim(),
    obsidian_filename_template: $("obsidian_filename_template").value.trim(),
    obsidian_create_entities: $("obsidian_create_entities").checked,
    notebooklm_sync_enabled: $("notebooklm_sync_enabled").checked,
    notebooklm_master_doc_enabled: $("notebooklm_master_doc_enabled").checked,
    notebooklm_drive_folder_id: $("notebooklm_drive_folder_id").value.trim(),
  };
  const anthropic = $("anthropic_api_key").value.trim();
  if (anthropic) payload.anthropic_api_key = anthropic;
  const nim = $("nim_api_key").value.trim();
  if (nim) payload.nim_api_key = nim;
  const runpod = $("runpod_api_key").value.trim();
  if (runpod) payload.runpod_api_key = runpod;
  const hfToken = $("hf_token").value.trim();
  if (hfToken) payload.hf_token = hfToken;

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

async function initializeNotebookLM() {
  const button = $("notebooklm-initialize");
  button.disabled = true;
  button.textContent = "Ouverture de Google…";
  try {
    // Le dossier éventuel doit être enregistré avant que le serveur crée le
    // Doc maître ; sinon il serait créé à la racine de Drive par défaut.
    await api("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        notebooklm_drive_folder_id: $("notebooklm_drive_folder_id").value.trim(),
      }),
    });
    const result = await api("/api/notebooklm/initialize", { method: "POST" });
    state.settings = await api("/api/settings");
    $("notebooklm_master_doc_id").value = result.master_doc_id;
    $("notebooklm_sync_enabled").checked = true;
    $("notebooklm-settings-state").textContent = result.detail;
    toast(result.detail);
  } catch (error) {
    $("notebooklm-settings-state").textContent = error.message;
    toast(error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = "Initialiser avec Google";
  }
}

async function testNotebookLMSync() {
  const button = $("notebooklm-test");
  button.disabled = true;
  try {
    const result = await api("/api/notebooklm/sync", { method: "POST" });
    $("notebooklm-settings-state").textContent = result.detail;
    toast(result.detail);
  } catch (error) {
    $("notebooklm-settings-state").textContent = error.message;
    toast(error.message, true);
  } finally {
    button.disabled = false;
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
  document.addEventListener("click", () => {
    menu.hidden = true;
    $("secondary-actions-menu").hidden = true;
  });
  menu.querySelectorAll("a").forEach((item) =>
    item.addEventListener("click", () => {
      if (!state.detail) return;
      window.location.href = `/api/jobs/${state.detail.id}/download/${item.dataset.fmt}`;
      menu.hidden = true;
    })
  );

  async function launchProofread(button, mode = $("proofread").value) {
    const job = state.detail;
    if (!job) return;
    button.disabled = true;
    try {
      await api(`/api/jobs/${job.id}/proofread`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          proofread: mode,
          structure: $("structure").checked,
          verify: $("verify").checked,
        }),
      });
      toast(`Relecture ${mode === "nim" ? "NVIDIA NIM" : mode === "claude" ? "Claude" : ""} lancée.`);
      await refreshJobs();
    } catch (error) {
      toast(error.message, true);
    } finally {
      button.disabled = false;
    }
  }

  $("proofread-btn").addEventListener("click", () => {
    launchProofread($("proofread-btn"));
  });
  $("retry-claude-btn").addEventListener("click", () => {
    launchProofread($("retry-claude-btn"), "claude");
  });
  $("retry-nim-btn").addEventListener("click", () => {
    launchProofread($("retry-nim-btn"), "nim");
  });

  $("revision-btn").addEventListener("click", async () => {
    if (!state.detail) return;
    try {
      await api(`/api/jobs/${state.detail.id}/revision`, { method: "POST" });
      toast("Fiche de révision en cours de génération.");
      refreshJobs();
    } catch (error) { toast(error.message, true); }
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
    searchTimer = setTimeout(() => {
      refreshJobs().catch(() => {});
      refreshGlobalSearch().catch((error) => toast(error.message, true));
    }, 250);
  });

  $("manual-review-btn").addEventListener("click", async () => {
    const job = state.detail;
    if (!job) return;
    const status = job.manual_review_status === "in_progress" ? "completed" : "in_progress";
    try {
      const updated = await api(`/api/jobs/${job.id}/manual-review`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status }),
      });
      state.detail = updated;
      renderDetail();
      toast(status === "completed" ? "Repasse manuelle terminée." : "Repasse manuelle démarrée : écoutez et corrigez les blocs.");
    } catch (error) {
      toast(error.message, true);
    }
  });

  $("cancel-btn").addEventListener("click", async () => {
    const job = state.detail;
    if (!job) return;
    const button = $("cancel-btn");
    button.disabled = true;
    try {
      await api(`/api/jobs/${job.id}/cancel`, { method: "POST" });
      toast("Annulation demandée.");
      await refreshJobs();
    } catch (error) {
      toast(error.message, true);
      button.disabled = false;
    }
  });

  $("retry-btn").addEventListener("click", async () => {
    const job = state.detail;
    if (!job) return;
    const button = $("retry-btn");
    button.disabled = true;
    try {
      await api(`/api/jobs/${job.id}/retry`, { method: "POST" });
      toast("Reprise lancée.");
      await refreshJobs();
    } catch (error) {
      toast(error.message, true);
      button.disabled = false;
    }
  });

  $("notebooklm-btn").addEventListener("click", async () => {
    const job = state.detail;
    if (!job) return;
    const button = $("notebooklm-btn");
    button.disabled = true;
    try {
      await api(`/api/jobs/${job.id}/notebooklm-sync`, { method: "POST" });
      toast("Synchronisation NotebookLM lancée.");
      await refreshJobs();
      await selectJob(job.id, true);
    } catch (error) {
      toast(error.message, true);
      renderDetail();
    }
  });
  ["status-filter", "date-filter", "tag-filter"].forEach((id) =>
    $(id).addEventListener("input", renderJobs)
  );

  $("global-search-results").addEventListener("click", async (event) => {
    const result = event.target.closest(".global-search-result");
    if (!result) return;
    await selectJob(result.dataset.jobId);
    if (result.dataset.start !== "") seekTo(Number(result.dataset.start));
  });

  $("editor-search-input").addEventListener("input", () => { updateEditorMatches(); renderBlocks(); });
  $("blocks-more-btn").addEventListener("click", () => {
    state.blocksRenderLimit += 250;
    renderBlocks();
  });
  $("editor-search-prev").addEventListener("click", () => moveEditorMatch(-1));
  $("editor-search-next").addEventListener("click", () => moveEditorMatch(1));

  $("annotation-filter").addEventListener("change", renderAnnotations);
  $("annotation-list").addEventListener("click", (event) => {
    const action = event.target.dataset.annotationAction;
    const card = event.target.closest("[data-annotation-id]");
    if (!action || !card) return;
    const annotationId = card.dataset.annotationId;
    if (action === "jump") {
      focusBlock(event.target.dataset.blockId);
    } else if (action === "delete") {
      removeAnnotation(annotationId);
    } else if (action === "valider") {
      updateAnnotation(annotationId, { status: "valide" });
    } else if (action === "edit") {
      state.editingAnnotationId = annotationId;
      renderAnnotations();
      const textarea = card.querySelector(`[data-annotation-edit-id="${annotationId}"]`);
      if (textarea) { textarea.focus(); textarea.setSelectionRange(textarea.value.length, textarea.value.length); }
    } else if (action === "cancel-edit") {
      state.editingAnnotationId = null;
      renderAnnotations();
    } else if (action === "save-edit") {
      const textarea = card.querySelector(`[data-annotation-edit-id="${annotationId}"]`);
      const content = textarea ? textarea.value.trim() : "";
      state.editingAnnotationId = null;
      updateAnnotation(annotationId, { content });
    }
  });

  $("review-history-btn").addEventListener("click", async () => {
    const job = state.detail;
    if (!job) return;
    try {
      const data = await api(`/api/jobs/${job.id}/review-versions`);
      $("review-history-list").innerHTML = data.versions.length ? data.versions.map((version) =>
        `<article class="annotation-item"><p><b>Version ${version.version}</b> — ${escapeHtml(version.reason)}</p><small>${escapeHtml(formatDate(version.created_at))}</small><button class="btn btn-mini btn-ghost" data-restore-version="${version.id}">Restaurer</button></article>`
      ).join("") : `<p class="empty">Aucune version archivée.</p>`;
      $("review-history-dialog").showModal();
    } catch (error) { toast(error.message, true); }
  });
  $("review-history-close").addEventListener("click", () => $("review-history-dialog").close());
  $("review-history-list").addEventListener("click", async (event) => {
    const button = event.target.closest("[data-restore-version]");
    const job = state.detail;
    if (!button || !job || !window.confirm("Restaurer cette version ? La version actuelle sera archivée.")) return;
    try {
      const updated = await api(`/api/jobs/${job.id}/review-versions/${button.dataset.restoreVersion}/restore`, { method: "POST" });
      state.detail = updated; state.blocks = []; state.blocksJobId = null; state.annotationsJobId = null;
      $("review-history-dialog").close(); renderDetail(); toast("Version restaurée.");
    } catch (error) { toast(error.message, true); }
  });
  $("annotation-list").addEventListener("change", (event) => {
    if (event.target.dataset.annotationAction !== "status") return;
    const card = event.target.closest("[data-annotation-id]");
    if (card) updateAnnotation(card.dataset.annotationId, { status: event.target.value });
  });

  $("add-job-tag-btn").addEventListener("click", addJobTag);
  $("job-tag-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); addJobTag(); }
  });
  $("job-tags").addEventListener("click", (event) => {
    const button = event.target.closest("[data-tag]");
    if (!button || !state.detail) return;
    saveTagsForJob(state.detail.id, tagsForJob(state.detail.id).filter((tag) => tag !== button.dataset.tag));
    renderJobTags(state.detail); renderJobs();
  });

  const secondaryMenu = $("secondary-actions-menu");
  $("secondary-actions-btn").addEventListener("click", (event) => {
    event.stopPropagation(); secondaryMenu.hidden = !secondaryMenu.hidden;
  });
  secondaryMenu.addEventListener("click", (event) => {
    const action = event.target.dataset.secondaryAction;
    if (action === "download") $("download-btn").click();
    if (action === "delete") $("delete-btn").click();
    secondaryMenu.hidden = true;
  });

  $("open-settings").addEventListener("click", openSettings);
  $("quit-app").addEventListener("click", async () => {
    if (!window.confirm("Fermer l'application ? Le serveur s'arrêtera complètement.")) return;
    try {
      await api("/api/shutdown", { method: "POST" });
    } catch (_) { /* le serveur s'arrête avant de répondre proprement */ }
    document.body.innerHTML = "<p style=\"padding:2rem;font:1.1rem system-ui\">"
      + "L'application est arrêtée. Vous pouvez fermer cette fenêtre.</p>";
    window.close();
  });
  $("update-btn").addEventListener("click", async () => {
    const button = $("update-btn");
    if (!window.confirm(`Installer la mise à jour ${button.dataset.version || ""} ? L'application va redémarrer.`)) return;
    button.disabled = true;
    button.textContent = "Téléchargement…";
    try {
      await api("/api/update", { method: "POST" });
      const poll = window.setInterval(async () => {
        try {
          const update = await api("/api/update");
          renderUpdateProgress(update.installation);
          if (["failed", "restarting"].includes(update.installation?.phase)) window.clearInterval(poll);
        } catch (_) { window.clearInterval(poll); }
      }, 750);
    } catch (error) { button.disabled = false; button.textContent = "Mise à jour disponible"; toast(error.message, true); }
  });
  $("settings-save").addEventListener("click", saveSettings);
  $("settings-cancel").addEventListener("click", () => $("settings-dialog").close());
  $("notebooklm-initialize").addEventListener("click", initializeNotebookLM);
  $("notebooklm-test").addEventListener("click", testNotebookLMSync);
  document.querySelectorAll(".settings-section").forEach((section) => {
    section.addEventListener("toggle", () => {
      if (!section.open) return;
      document.querySelectorAll(".settings-section").forEach((other) => {
        if (other !== section) other.open = false;
      });
    });
  });

  $("obsidian_layout").addEventListener("change", updateObsidianLayoutUI);
  $("obsidian_filename_style").addEventListener("change", updateObsidianFilenameUI);
  $("browse-vault-btn").addEventListener("click", openFolderBrowser);
  $("folder-browser-choose").addEventListener("click", chooseFolderBrowserPath);
  $("folder-browser-cancel").addEventListener("click", () => { $("folder-browser").hidden = true; });

  document.addEventListener("click", (event) => {
    const findingTime = event.target.closest('[data-action="focus-finding"]');
    if (findingTime) { focusBlock(findingTime.dataset.blockId); return; }
    const button = event.target.closest(".finding-expand");
    if (!button) return;
    const point = findingRegistry[Number(button.dataset.idx)];
    if (point) { if (point.block_id) focusBlock(point.block_id); openPassage(point); }
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
  initBlockContextMenu();
  initKeyboardShortcuts();
  try {
    await loadStatus();
    await refreshJobs();
    listenEvents();
  } catch (error) {
    toast(`Impossible de contacter le serveur : ${error.message}`, true);
  }
})();
