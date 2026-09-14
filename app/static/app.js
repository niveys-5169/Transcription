/* Transcription de cours — logique de la page. */
"use strict";

const $ = (id) => document.getElementById(id);

const state = {
  jobs: [],
  selected: null,
  detail: null,
  pending: [],
  settings: {},
  tab: "clean",
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
          <span class="badge ${job.status}">${
            job.duration ? clock(job.duration) : humanSize(job.size_bytes)
          }</span>
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
  state.selected = jobId;
  if (!keepTab) state.tab = "clean";
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

function renderDetail() {
  const job = state.detail;
  $("result-empty").hidden = Boolean(job);
  $("result-body").hidden = !job;
  if (!job) return;

  $("result-title").textContent = job.title || job.filename;
  $("result-meta").textContent = metaLine(job);

  const errorBanner = $("result-error");
  errorBanner.hidden = !job.error;
  errorBanner.textContent = job.error || "";

  const summary = Array.isArray(job.summary) ? job.summary : [];
  $("result-summary").hidden = summary.length === 0;
  $("summary-list").innerHTML = summary.map((point) => `<li>${escapeHtml(point)}</li>`).join("");

  $("panel-clean").innerHTML = job.clean_text
    ? renderTranscript(job.clean_text)
    : `<p class="empty">${
        job.status === "done" ? "Aucun texte relu." : "Traitement en cours…"
      }</p>`;
  $("panel-raw").innerHTML = renderTranscript(job.raw_text, "is-raw");

  const segments = job.segments || [];
  $("panel-segments").innerHTML = segments.length
    ? `<div class="segments">${segments.map((segment) => `
        <div class="segment">
          <time>${clock(segment.start)}</time>
          <span>${escapeHtml(segment.text)}</span>
        </div>`).join("")}</div>`
    : `<p class="empty">Aucun segment.</p>`;

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
    const vaultName = state.settings.obsidian_vault_path.split(/[\\/]/).filter(Boolean).pop() || "";
    obsidianLink.href = `obsidian://open?vault=${encodeURIComponent(vaultName)}&file=${encodeURIComponent(job.obsidian_path.replace(/\.md$/, ""))}`;
  } else {
    obsidianLink.hidden = true;
  }

  const player = $("audio-player");
  const audioUrl = `/api/jobs/${job.id}/audio`;
  if (player.dataset.job !== job.id) {
    player.dataset.job = job.id;
    player.src = audioUrl;
  }

  showTab(state.tab);
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

function renderFindingCard(point) {
  return `
    <div class="finding ${escapeHtml(point.severity)}">
      <div class="finding-head">
        <time>${clock(point.start)}</time>
        <span class="finding-kind">${escapeHtml(KIND_LABELS[point.kind] || point.kind)}</span>
        <span class="finding-kind">${escapeHtml(sourceLabel(point))}</span>
      </div>
      <p class="finding-message">${escapeHtml(point.message)}</p>
      ${point.raw_excerpt || point.clean_excerpt ? `
        <div class="finding-quotes">
          ${point.raw_excerpt ? `<div class="finding-quote"><b>Brut</b><span>${escapeHtml(point.raw_excerpt)}</span></div>` : ""}
          ${point.clean_excerpt ? `<div class="finding-quote"><b>Relu</b><span>${escapeHtml(point.clean_excerpt)}</span></div>` : ""}
        </div>` : ""}
    </div>`;
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
  const badge = $("sources-count");
  badge.hidden = points.length === 0;
  badge.textContent = String(points.length);

  const panel = $("panel-sources");
  const report = job.factcheck_report;

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
      ${points.length
        ? `<span class="badge error">${points.length} point${points.length > 1 ? "s" : ""} non confirmé${points.length > 1 ? "s" : ""}</span>`
        : `<span class="verify-ok">Tout ce qui a été vérifié est confirmé</span>`}
    </div>`;

  if (!points.length) {
    panel.innerHTML = `${entete}
      <p class="meta">
        Les notes de bas de page du texte relu détaillent les sources. Une
        vérification automatique reste une aide, pas une garantie.
      </p>`;
    return;
  }

  panel.innerHTML = entete + `
    <p class="meta">
      Le texte relu porte un appel de note (<code>[^v…]</code>) à chacun de
      ces points ; les sources consultées y sont citées.
    </p>` + points.map(renderFindingCard).join("");
}

function showTab(name) {
  state.tab = name;
  document.querySelectorAll(".tab").forEach((tab) =>
    tab.classList.toggle("is-active", tab.dataset.tab === name)
  );
  ["clean", "raw", "segments", "verify", "sources", "audio"].forEach((key) => {
    $(`panel-${key}`).hidden = key !== name;
  });
}

function currentText() {
  const job = state.detail;
  if (!job) return "";
  if (state.tab === "raw") return job.raw_text || "";
  if (state.tab === "segments") {
    return (job.segments || [])
      .map((segment) => `[${clock(segment.start)}] ${segment.text}`)
      .join("\n");
  }
  return job.clean_text || job.raw_text || "";
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

/* ------------------------------------------------------------- actions */

function initActions() {
  $("upload-form").addEventListener("submit", (event) => submitFiles(event, false));
  $("one-click-btn").addEventListener("click", () => submitFiles(null, true));
  $("engine").addEventListener("change", updateEngineDetail);

  document.querySelectorAll(".tab").forEach((tab) =>
    tab.addEventListener("click", () => showTab(tab.dataset.tab))
  );

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
}

/* ------------------------------------------------------------ init */

(async function main() {
  initDropzone();
  initActions();
  try {
    await loadStatus();
    await refreshJobs();
    listenEvents();
  } catch (error) {
    toast(`Impossible de contacter le serveur : ${error.message}`, true);
  }
})();
