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

  const pills = [
    pill(status.ffmpeg.available, "Extraction audio", status.ffmpeg.detail),
    pill(status.engines.local.available, "Moteur local", status.engines.local.detail),
    pill(status.engines.runpod.available, "RunPod", status.engines.runpod.detail, true),
    pill(claude.available, "Relecture Claude", claude.detail, true),
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

async function submitFiles(event) {
  event.preventDefault();
  if (!state.pending.length) return;

  const button = $("submit-btn");
  button.disabled = true;
  const files = state.pending.slice();
  let sent = 0;

  for (const file of files) {
    const form = new FormData();
    form.append("file", file);
    form.append("engine", $("engine").value);
    form.append("model", $("model").value);
    form.append("language", $("language").value);
    form.append("proofread", $("proofread").value);
    form.append("structure", $("structure").checked ? "true" : "false");
    form.append("verify", $("verify").checked ? "true" : "false");
    form.append("chain", $("chain").checked ? "true" : "false");

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

  button.textContent = "Lancer la transcription";
  setPending([]);
  $("file-input").value = "";
  if (sent) {
    toast(sent > 1 ? `${sent} fichiers mis en file.` : "Transcription lancée.");
    refreshJobs();
  }
}

/* --------------------------------------------------------------- file */

function statusLabel(job) {
  return {
    queued: "En attente",
    running: job.stage || "En cours",
    transcribed: "Transcrit — à relire",
    done: "Terminé",
    error: "Erreur",
    canceled: "Annulé",
  }[job.status] || job.status;
}

// Un travail transcrit est déjà exploitable : texte brut, segments et
// sous-titres sont disponibles, la relecture peut venir plus tard.
function isReadable(job) {
  return job.status === "transcribed" || job.status === "done";
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

  // La relecture est une étape à part : on peut la lancer, ou la relancer
  // avec d'autres réglages, sur n'importe quel travail déjà transcrit.
  const proofreadBtn = $("proofread-btn");
  proofreadBtn.hidden = !isReadable(job);
  proofreadBtn.textContent =
    job.status === "done" ? "Relancer la relecture" : "Relire maintenant";

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
};

function renderVerification(job) {
  const rapport = job.verification || {};
  const points = Array.isArray(rapport.findings) ? rapport.findings : [];
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

  panel.innerHTML = entete + points.map((point) => `
    <div class="finding ${escapeHtml(point.severity)}">
      <div class="finding-head">
        <time>${clock(point.start)}</time>
        <span class="finding-kind">${escapeHtml(KIND_LABELS[point.kind] || point.kind)}</span>
        <span class="finding-kind">${point.source === "claude" ? "Claude" : "règle"}</span>
      </div>
      <p class="finding-message">${escapeHtml(point.message)}</p>
      ${point.raw_excerpt || point.clean_excerpt ? `
        <div class="finding-quotes">
          ${point.raw_excerpt ? `<div class="finding-quote"><b>Brut</b><span>${escapeHtml(point.raw_excerpt)}</span></div>` : ""}
          ${point.clean_excerpt ? `<div class="finding-quote"><b>Relu</b><span>${escapeHtml(point.clean_excerpt)}</span></div>` : ""}
        </div>` : ""}
    </div>`).join("");
}

function showTab(name) {
  state.tab = name;
  document.querySelectorAll(".tab").forEach((tab) =>
    tab.classList.toggle("is-active", tab.dataset.tab === name)
  );
  ["clean", "raw", "segments", "verify", "audio"].forEach((key) => {
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

function openSettings() {
  const settings = state.settings;
  $("proofread_model").value = settings.proofread_model || "";
  $("proofread_effort").value = settings.proofread_effort || "medium";
  $("runpod_endpoint_id").value = settings.runpod_endpoint_id || "";
  $("runpod_chunk_seconds").value = settings.runpod_chunk_seconds || 240;
  $("keep_media").checked = Boolean(settings.keep_media);
  $("anthropic_api_key").value = "";
  $("runpod_api_key").value = "";
  $("anthropic-state").textContent = settings.anthropic_api_key_set
    ? "Une clé est enregistrée. Laissez vide pour la conserver."
    : "Aucune clé enregistrée.";
  $("runpod-state").textContent = settings.runpod_api_key_set
    ? "Une clé est enregistrée. Laissez vide pour la conserver."
    : "Aucune clé enregistrée.";
  $("settings-dialog").showModal();
}

async function saveSettings() {
  const payload = {
    proofread_model: $("proofread_model").value.trim(),
    proofread_effort: $("proofread_effort").value,
    runpod_endpoint_id: $("runpod_endpoint_id").value.trim(),
    runpod_chunk_seconds: Number($("runpod_chunk_seconds").value) || 240,
    keep_media: $("keep_media").checked,
    default_engine: $("engine").value,
    default_model: $("model").value,
    language: $("language").value,
    default_proofread: $("proofread").value,
    structure_output: $("structure").checked,
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
  $("upload-form").addEventListener("submit", submitFiles);
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
