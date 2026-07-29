const jobsEl = document.querySelector("#jobs");
const detailEl = document.querySelector("#detail");
const nodesEl = document.querySelector("#nodes");
const tokenEl = document.querySelector("#token");

function headers() {
  return {
    "Authorization": `Bearer ${tokenEl.value}`,
    "Content-Type": "application/json"
  };
}

async function api(path, options = {}) {
  const response = await fetch(path, { ...options, headers: { ...headers(), ...(options.headers || {}) } });
  if (!response.ok) throw new Error(`${response.status} ${await response.text()}`);
  return response.json();
}

function fmtTime(ts) {
  return new Date(ts * 1000).toLocaleTimeString();
}

async function refresh() {
  const [jobsData, nodesData] = await Promise.all([api("/api/jobs"), api("/api/nodes")]);
  jobsEl.innerHTML = "";
  for (const job of jobsData.jobs) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><span class="pill ${job.status}">${job.status}</span></td>
      <td>${job.objective}<br><small>${fmtTime(job.updated_at)}</small></td>
      <td>${job.runner_id || ""}</td>
      <td><code>${job.id}</code></td>
    `;
    tr.addEventListener("click", async () => {
      const detail = await api(`/api/jobs/${job.id}`);
      detailEl.textContent = JSON.stringify(detail, null, 2);
    });
    jobsEl.appendChild(tr);
  }
  nodesEl.innerHTML = "";
  for (const node of nodesData.nodes) {
    const block = document.createElement("div");
    block.className = "node-card";
    const tools = node.metadata?.tools || {};
    const models = node.metadata?.models?.items || [];
    const selectedModel = node.selected_model?.id || "";
    const compat = node.compatibility || {};
    const compatRows = Object.entries(compat).map(([name, info]) => `
      <code>${info.label || name}</code>
      <span class="${info.ok ? "yes" : "no"}">${info.ok ? "ready" : `missing ${(info.missing_tools || []).concat((info.missing_any_tools || []).map(g => g.join(" or "))).join(", ") || "capability"}`}</span>
    `).join("");
    const rows = Object.entries(tools).map(([name, info]) => `
      <code>${name}</code>
      <span class="${info.available ? "yes" : "no"}">${info.available ? "available" : "missing"}</span>
    `).join("");
    const modelOptions = [
      `<option value="">Runner default</option>`,
      ...models.map(model => `<option value="${model.id}" ${model.id === selectedModel ? "selected" : ""}>${model.name || model.id}${model.available === false ? " (missing)" : ""}</option>`)
    ].join("");
    block.innerHTML = `
      <strong>${node.id}</strong><br>
      <small>${node.metadata?.platform || ""}</small>
      <div class="model-row">
        <select data-node-model="${node.id}">${modelOptions}</select>
        <button class="secondary" data-save-model="${node.id}">Use Model</button>
      </div>
      <small>Selected: ${node.selected_model?.name || node.selected_model?.id || "runner default"}</small>
      <div class="compat-grid">${compatRows}</div>
      <div class="tool-grid">${rows}</div>
    `;
    nodesEl.appendChild(block);
  }
  for (const button of nodesEl.querySelectorAll("[data-save-model]")) {
    button.addEventListener("click", async () => {
      const nodeId = button.getAttribute("data-save-model");
      const select = nodesEl.querySelector(`[data-node-model="${nodeId}"]`);
      await api(`/api/nodes/${nodeId}/model`, {
        method: "POST",
        body: JSON.stringify({ selected_model_id: select.value || null })
      });
      await refresh();
    });
  }
}

async function createJob() {
  const objective = document.querySelector("#objective").value;
  const source = document.querySelector("#source").value;
  const job = await api("/api/jobs", {
    method: "POST",
    body: JSON.stringify({
      objective,
      capability: "python",
      payload: { source, timeout_seconds: 15 }
    })
  });
  detailEl.textContent = JSON.stringify(job, null, 2);
  await refresh();
}

document.querySelector("#refresh").addEventListener("click", () => refresh().catch(err => alert(err.message)));
document.querySelector("#create").addEventListener("click", () => createJob().catch(err => alert(err.message)));
refresh().catch(err => {
  detailEl.textContent = err.message;
});
