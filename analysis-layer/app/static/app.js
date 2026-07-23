const listEl = document.getElementById("list");
const rowTemplate = document.getElementById("row-template");
const applyBtn = document.getElementById("apply-btn");
const rescanBtn = document.getElementById("rescan-btn");
const discoverBtn = document.getElementById("discover-btn");
const discoverPanel = document.getElementById("discover-panel");
const discoverReloadBtn = document.getElementById("discover-reload-btn");
const discoverCloseBtn = document.getElementById("discover-close-btn");

let currentData = [];
const selectedContainers = new Set();
const selectedPatchContainers = new Set();
const expandedContainers = new Set();

async function fetchStatus() {
  const res = await fetch("/api/status");
  currentData = await res.json();
  render();
}

function updateApplyButtonState() {
  applyBtn.disabled = selectedContainers.size === 0;
}

function render() {
  listEl.innerHTML = "";

  const pending = currentData.filter((r) => r.status === "pending");
  const others = currentData.filter((r) => r.status !== "pending");

  if (pending.length === 0 && others.length === 0) {
    listEl.innerHTML = '<p class="empty-state">Keine Updates erkannt. Alles aktuell.</p>';
    return;
  }

  for (const row of [...pending, ...others]) {
    listEl.appendChild(renderRow(row));
  }

  updateApplyButtonState();
}

function renderRow(row) {
  const node = rowTemplate.content.cloneNode(true);
  const section = node.querySelector(".update-row");

  const checkbox = node.querySelector(".select-box");
  checkbox.dataset.container = row.container;
  checkbox.disabled = row.status !== "pending";
  checkbox.checked = selectedContainers.has(row.container);
  checkbox.addEventListener("change", () => {
    if (checkbox.checked) selectedContainers.add(row.container);
    else selectedContainers.delete(row.container);
    updateApplyButtonState();
  });

  node.querySelector(".container-name").textContent = row.container;
  node.querySelector(".stack-name").textContent = row.portainer_stack_name
    ? `Stack: ${row.portainer_stack_name}`
    : row.configured
      ? ""
      : "(nicht in stacks.yml konfiguriert)";
  node.querySelector(".version").textContent = `${row.current_version || "?"} -> ${row.new_version || "?"}${row.version_note ? " *" : ""}`;
  if (row.version_note) {
    node.querySelector(".version").title = row.version_note;
  }

  const riskBadge = node.querySelector(".risk-badge");
  const risk = row.risk || "unknown";
  riskBadge.textContent = { none: "unbedenklich", minor: "kleine Aenderungen", major: "groessere Aenderungen", breaking: "breaking changes", unknown: "unbekannt" }[risk] || risk;
  riskBadge.classList.add(`risk-${risk}`);

  const statusBadge = node.querySelector(".status-badge");
  statusBadge.textContent = { pending: "offen", applied: "aktualisiert", dismissed: "ignoriert" }[row.status] || row.status;
  statusBadge.classList.add(`status-${row.status}`);

  const details = node.querySelector(".row-details");
  details.hidden = !expandedContainers.has(row.container);
  node.querySelector(".toggle-details").addEventListener("click", () => {
    details.hidden = !details.hidden;
    if (details.hidden) expandedContainers.delete(row.container);
    else expandedContainers.add(row.container);
  });

  node.querySelector(".summary").textContent = row.summary || "Analyse laeuft noch oder wurde nicht durchgefuehrt ...";

  if (row.version_note) {
    const noteEl = node.querySelector(".version-note");
    noteEl.hidden = false;
    noteEl.textContent = `* ${row.version_note}`;
  }

  if (row.compose_change_needed) {
    const composeBlock = node.querySelector(".compose-block");
    composeBlock.hidden = false;
    node.querySelector(".compose-explanation").textContent = row.compose_change_explanation || "";
    node.querySelector(".compose-patch").textContent = row.compose_patch || "(kein konkreter Vorschlag - bitte manuell pruefen)";
    const patchBox = node.querySelector(".apply-patch-box");
    patchBox.dataset.container = row.container;
    patchBox.checked = selectedPatchContainers.has(row.container);
    patchBox.addEventListener("change", () => {
      if (patchBox.checked) selectedPatchContainers.add(row.container);
      else selectedPatchContainers.delete(row.container);
    });
    if (!row.compose_patch) patchBox.disabled = true;
  }

  node.querySelector(".dismiss-btn").addEventListener("click", async () => {
    await fetch("/api/dismiss", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ container: row.container }),
    });
    selectedContainers.delete(row.container);
    selectedPatchContainers.delete(row.container);
    fetchStatus();
  });

  node.querySelector(".reanalyze-btn").addEventListener("click", async (e) => {
    e.target.textContent = "...";
    await fetch(`/api/analyze/${encodeURIComponent(row.container)}`, { method: "POST" });
    fetchStatus();
  });

  if (row.status !== "pending") {
    section.style.opacity = "0.6";
  }

  return node;
}

rescanBtn.addEventListener("click", async () => {
  rescanBtn.textContent = "Pruefe ...";
  rescanBtn.disabled = true;
  await fetch("/api/rescan", { method: "POST" });
  await fetchStatus();
  rescanBtn.textContent = "Jetzt neu pruefen";
  rescanBtn.disabled = false;
});

applyBtn.addEventListener("click", async () => {
  const items = [...selectedContainers].map((container) => ({
    container,
    apply_compose_patch: selectedPatchContainers.has(container),
  }));

  if (!items.length) return;
  if (!confirm(`${items.length} Stack(s) jetzt aktualisieren und redeployen?`)) return;

  applyBtn.disabled = true;
  applyBtn.textContent = "Wird aktualisiert ...";
  const res = await fetch("/api/apply", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ items }),
  });
  const data = await res.json();
  const failed = data.results.filter((r) => !r.ok);
  if (failed.length) {
    alert("Fehler bei: " + failed.map((f) => `${f.container} (${f.error})`).join(", "));
  }
  for (const item of items) {
    selectedContainers.delete(item.container);
    selectedPatchContainers.delete(item.container);
  }
  applyBtn.textContent = "Ausgewaehlte aktualisieren";
  await fetchStatus();
});

discoverBtn.addEventListener("click", async () => {
  discoverBtn.textContent = "Lese Portainer aus ...";
  discoverBtn.disabled = true;
  try {
    const res = await fetch("/api/discover-stacks", { method: "POST" });
    const data = await res.json();
    if (!res.ok) {
      alert("Fehler: " + (data.detail || res.statusText));
      return;
    }

    const parts = [];
    parts.push(`${data.added.length} neue Container gefunden.`);
    if (data.updated.length) parts.push(`${data.updated.length} bestehende Eintraege aktualisiert (Stack/Environment geaendert).`);
    if (data.auto_repo.length) parts.push(`${data.auto_repo.length}x github_repo automatisch erkannt (org.opencontainers.image.source-Label).`);
    if (data.needs_repo.length) parts.push(`${data.needs_repo.length}x github_repo noch offen (manuell ergaenzen): ${data.needs_repo.join(", ")}`);
    if (data.missing.length) parts.push(`${data.missing.length} bisher konfigurierte Container nicht mehr gefunden (unveraendert gelassen): ${data.missing.join(", ")}`);
    if (data.errors.length) parts.push(`Fehler bei einzelnen Environments: ${data.errors.join(" | ")}`);
    parts.push(`Geschrieben nach: ${data.written_to} (im Container - auf dem NAS unter deinem CONFIG_DIR/data/analysis/).`);

    discoverPanel.querySelector(".discover-summary").innerHTML = parts.map((p) => `<p>${p}</p>`).join("");
    discoverPanel.querySelector(".discover-yaml").textContent = data.yaml;
    discoverPanel.hidden = false;
    discoverPanel.scrollIntoView({ behavior: "smooth" });
  } catch (err) {
    alert("Fehler bei der Discovery: " + err);
  } finally {
    discoverBtn.textContent = "Stacks entdecken";
    discoverBtn.disabled = false;
  }
});

discoverReloadBtn.addEventListener("click", async () => {
  const res = await fetch("/api/reload-config", { method: "POST" });
  const data = await res.json();
  alert(`stacks.yml neu geladen: ${data.containers.length} Container konfiguriert.`);
  fetchStatus();
});

discoverCloseBtn.addEventListener("click", () => {
  discoverPanel.hidden = true;
});

fetchStatus();
setInterval(fetchStatus, 30000);
