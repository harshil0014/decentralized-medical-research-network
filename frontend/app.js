const state = {
  token: sessionStorage.getItem("medical_token") || "",
  role: sessionStorage.getItem("medical_role") || "",
  datasets: [],
  plaintextDownloadsEnabled: false,
};

const $ = (id) => document.getElementById(id);

function showToast(message) {
  const el = $("toast");
  el.textContent = message;
  el.classList.remove("hidden");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => el.classList.add("hidden"), 2600);
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (state.token) headers.set("Authorization", `Bearer ${state.token}`);
  const response = await fetch(path, { ...options, headers });

  if (!response.ok) {
    let message = `${response.status}`;
    try {
      const data = await response.json();
      message = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail || data);
    } catch (_) {}
    throw new Error(message);
  }

  return response;
}

async function apiJson(path, options = {}) {
  const response = await api(path, options);
  const text = await response.text();
  return text ? JSON.parse(text) : {};
}

function jsonBody(data) {
  return {
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  };
}

function button(label, onClick, className = "") {
  const b = document.createElement("button");
  b.textContent = label;
  if (className) b.className = className;
  b.addEventListener("click", onClick);
  return b;
}

function cell(value = "") {
  const td = document.createElement("td");
  td.textContent = value ?? "";
  return td;
}

function setView(name) {
  document.querySelectorAll(".view").forEach((el) => el.classList.add("hidden"));
  document.querySelectorAll(".nav-button").forEach((el) => el.classList.remove("active"));
  $(`${name}View`).classList.remove("hidden");
  document.querySelector(`[data-view="${name}"]`).classList.add("active");
}

function outputBox(id) {
  const pre = document.createElement("pre");
  pre.id = id;
  pre.className = "output";
  pre.textContent = "";
  return pre;
}

async function login() {
  const token = $("tokenInput").value.trim();
  $("loginError").textContent = "";
  if (!token) return;

  state.token = token;
  try {
    const me = await apiJson("/auth/me");
    state.role = me.role;
    state.plaintextDownloadsEnabled = me.plaintextDownloadsEnabled === true;
    sessionStorage.setItem("medical_token", state.token);
    sessionStorage.setItem("medical_role", state.role);
    openApp();
  } catch (error) {
    state.token = "";
    $("loginError").textContent = error.message;
  }
}

function logout() {
  sessionStorage.removeItem("medical_token");
  sessionStorage.removeItem("medical_role");
  state.token = "";
  state.role = "";
  state.plaintextDownloadsEnabled = false;
  $("appView").classList.add("hidden");
  $("loginView").classList.remove("hidden");
  $("tokenInput").value = "";
}

async function openApp() {
  $("loginView").classList.add("hidden");
  $("appView").classList.remove("hidden");
  $("roleBadge").textContent = state.role;
  renderAll();
  await refreshDatasets();
}

async function refreshDatasets() {
  try {
    const data = await apiJson("/datasets");
    state.datasets = Array.isArray(data) ? data : (data.datasets || []);
    renderDatasetTable();
  } catch (error) {
    showToast(error.message);
  }
}

function renderAll() {
  renderDatasetsView();
  renderRequestsView();
  renderHEView();
}

function renderDatasetsView() {
  const root = $("datasetsView");
  root.replaceChildren();

  const toolbar = document.createElement("div");
  toolbar.className = "toolbar";
  const title = document.createElement("h2");
  title.textContent = "Datasets";
  toolbar.append(title, button("Refresh", refreshDatasets));
  root.append(toolbar);

  if (state.role === "hospital") root.append(buildUploadCard());

  const card = document.createElement("div");
  card.className = "card table-wrap";
  const table = document.createElement("table");
  table.id = "datasetsTable";
  card.append(table);
  root.append(card);
  renderDatasetTable();
}

function buildUploadCard() {
  const card = document.createElement("div");
  card.className = "card";
  const h = document.createElement("h3");
  h.textContent = "Upload";

  const form = document.createElement("form");
  form.className = "form-grid";
  form.innerHTML = `
    <input name="dataset_id" placeholder="Local dataset label (never written on-chain)" required>
    <input name="data_type" placeholder="Data type" value="LAB_CSV" required>
    <input class="full" name="metadata_summary" placeholder="Metadata" required>
    <select name="consent_state"><option>ACTIVE</option><option>REVOKED</option></select>
    <input name="file" type="file" required>
    <label class="full"><input name="visual_phi_reviewed" type="checkbox" value="true"> CT/MR pixel data visually reviewed; no burned-in identifiers or recognizable features remain</label>
    <div class="full form-actions"><button class="primary" type="submit">Upload</button></div>
  `;

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const data = await apiJson("/datasets/upload", {
        method: "POST",
        body: new FormData(form),
      });
      showToast(`Uploaded ${data.datasetId}`);
      form.reset();
      await refreshDatasets();
    } catch (error) {
      showToast(error.message);
    }
  });

  card.append(h, form);
  return card;
}

function renderDatasetTable() {
  const table = $("datasetsTable");
  if (!table) return;
  table.replaceChildren();

  const head = document.createElement("thead");
  head.innerHTML = "<tr><th>ID</th><th>Type</th><th>Consent</th><th>Owner Wallet</th><th>Actions</th></tr>";
  table.append(head);

  const body = document.createElement("tbody");

  const rows = [...state.datasets].sort(
    (a, b) => new Date(b.createdAt || 0) - new Date(a.createdAt || 0)
  );

  for (const row of rows) {
    const tr = document.createElement("tr");
    tr.append(
      cell(row.datasetId),
      cell(row.dataType),
      cell(row.consentState),
      cell(row.ownerAddress ? `${row.ownerAddress.slice(0, 8)}...${row.ownerAddress.slice(-6)}` : row.ownerOrg),
    );

    const actionCell = document.createElement("td");
    const actions = document.createElement("div");
    actions.className = "actions";

    if (state.role === "hospital") {
      actions.append(
        button("Active", () => setConsent(row.datasetId, "ACTIVE")),
        button("Revoke", () => setConsent(row.datasetId, "REVOKED"), "danger"),
        button("Rotate", () => rotateKey(row.datasetId)),
      );
    } else {
      actions.append(button("Request", () => {
        setView("requests");
        const input = $("requestDatasetId");
        if (input) input.value = row.datasetId;
      }));
    }

    actionCell.append(actions);
    tr.append(actionCell);
    body.append(tr);
  }
  table.append(body);
}

async function setConsent(datasetId, consentState) {
  try {
    await apiJson(`/datasets/${encodeURIComponent(datasetId)}/consent`, {
      method: "POST",
      ...jsonBody({ consent_state: consentState }),
    });
    showToast(consentState);
    await refreshDatasets();
  } catch (error) {
    showToast(error.message);
  }
}

async function rotateKey(datasetId) {
  try {
    const data = await apiJson(`/datasets/${encodeURIComponent(datasetId)}/rotate-key`, { method: "POST" });
    showToast(`Key v${data.newKeyVersion || data.keyVersion || "updated"}`);
  } catch (error) {
    showToast(error.message);
  }
}

function renderRequestsView() {
  const root = $("requestsView");
  root.replaceChildren();

  const toolbar = document.createElement("div");
  toolbar.className = "toolbar";
  const title = document.createElement("h2");
  title.textContent = "Requests";
  toolbar.append(title);
  root.append(toolbar);

  if (state.role === "researcher") root.append(buildResearchRequestCard());
  root.append(buildRequestLookupCard());
}

function buildResearchRequestCard() {
  const card = document.createElement("div");
  card.className = "card";
  const h = document.createElement("h3");
  h.textContent = "New Request";

  const form = document.createElement("form");
  form.className = "form-grid";
  form.innerHTML = `
    <input id="requestDatasetId" name="dataset_id" placeholder="Opaque Dataset ID" required>
    <input class="full" name="purpose" placeholder="Purpose" required>
    <div class="full form-actions"><button class="primary" type="submit">Create</button></div>
  `;

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const fd = new FormData(form);
    try {
      const data = await apiJson("/requests", {
        method: "POST",
        ...jsonBody({
          dataset_id: fd.get("dataset_id"),
          purpose: fd.get("purpose"),
        }),
      });
      $("requestLookupId").value = data.requestId || requestId;
      $("requestOutput").textContent = JSON.stringify(data, null, 2);
      showToast("Request created");
    } catch (error) {
      showToast(error.message);
    }
  });

  card.append(h, form);
  return card;
}

function buildRequestLookupCard() {
  const card = document.createElement("div");
  card.className = "card";
  const h = document.createElement("h3");
  h.textContent = "Request";

  const row = document.createElement("div");
  row.className = "form-grid";
  const input = document.createElement("input");
  input.id = "requestLookupId";
  input.placeholder = "Request ID";
  row.append(input);

  const actions = document.createElement("div");
  actions.className = "form-actions";
  actions.append(button("View", viewRequest));

  if (state.role === "hospital") {
    actions.append(
      button("Approve", () => decideRequest("approve"), "primary"),
      button("Revoke", () => decideRequest("revoke"), "danger"),
    );
  } else if (state.plaintextDownloadsEnabled) {
    actions.append(button("Download", downloadRequest));
  }

  row.append(actions);
  const out = outputBox("requestOutput");
  card.append(h, row, out);
  return card;
}

async function viewRequest() {
  const id = $("requestLookupId").value.trim();
  if (!id) return;
  try {
    const data = await apiJson(`/requests/${encodeURIComponent(id)}`);
    $("requestOutput").textContent = JSON.stringify(data, null, 2);
  } catch (error) {
    showToast(error.message);
  }
}

async function decideRequest(action) {
  const id = $("requestLookupId").value.trim();
  if (!id) return;
  try {
    const data = await apiJson(`/requests/${encodeURIComponent(id)}/${action}`, { method: "POST" });
    $("requestOutput").textContent = JSON.stringify(data, null, 2);
    showToast(action === "approve" ? "Approved" : "Revoked");
  } catch (error) {
    showToast(error.message);
  }
}

async function downloadRequest() {
  const id = $("requestLookupId").value.trim();
  if (!id) return;
  try {
    const response = await api(`/requests/${encodeURIComponent(id)}/download`);
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${id}-dataset`;
    document.body.append(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  } catch (error) {
    showToast(error.message);
  }
}

function renderHEView() {
  const root = $("heView");
  root.replaceChildren();

  const toolbar = document.createElement("div");
  toolbar.className = "toolbar";
  const title = document.createElement("h2");
  title.textContent = "HE";
  toolbar.append(title);
  root.append(toolbar);

  if (state.role === "hospital") {
    root.append(
      buildEncryptCard(),
      buildDecryptCard(),
      buildDicomSegmentInspectorCard(),
      buildDicomEncryptCard(),
      buildDicomDecryptCard(),
    );
  }
  if (state.role === "researcher") {
    root.append(buildComputeCard(), buildDicomComputeCard());
  }
  root.append(buildHELookupCard(), buildDicomHELookupCard());
}

function buildEncryptCard() {
  const card = document.createElement("div");
  card.className = "card";
  const h = document.createElement("h3");
  h.textContent = "Encrypt";
  const form = document.createElement("form");
  form.className = "form-grid";
  form.innerHTML = `
    <input name="dataset_id" placeholder="Dataset ID" required>
    <input name="request_id" placeholder="Request ID" required>
    <input class="full" name="metric" value="glucose_mg_dl" placeholder="Metric" required>
    <div class="full form-actions"><button class="primary" type="submit">Encrypt</button></div>
  `;
  const out = outputBox("encryptOutput");

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const fd = new FormData(form);
    try {
      const data = await apiJson("/he/glucose/encrypt", {
        method: "POST",
        ...jsonBody({
          dataset_id: fd.get("dataset_id"),
          request_id: fd.get("request_id"),
          metric: fd.get("metric"),
        }),
      });
      out.textContent = JSON.stringify(data, null, 2);
      const job = data.job_id;
      const decrypt = $("decryptJobId");
      const lookup = $("heLookupJobId");
      if (decrypt && job) decrypt.value = job;
      if (lookup && job) lookup.value = job;
      showToast("Encrypted");
    } catch (error) {
      showToast(error.message);
    }
  });

  card.append(h, form, out);
  return card;
}

function buildDecryptCard() {
  const card = document.createElement("div");
  card.className = "card";
  const h = document.createElement("h3");
  h.textContent = "Decrypt Result";
  const row = document.createElement("div");
  row.className = "form-grid";
  const input = document.createElement("input");
  input.id = "decryptJobId";
  input.placeholder = "Job ID";
  const actions = document.createElement("div");
  actions.className = "form-actions";
  actions.append(button("Decrypt", async () => {
    const id = input.value.trim();
    if (!id) return;
    try {
      const data = await apiJson(`/he/glucose/${encodeURIComponent(id)}/decrypt-average`, { method: "POST" });
      $("decryptOutput").textContent = JSON.stringify(data, null, 2);
    } catch (error) { showToast(error.message); }
  }, "primary"));
  row.append(input, actions);
  card.append(h, row, outputBox("decryptOutput"));
  return card;
}

function buildComputeCard() {
  const card = document.createElement("div");
  card.className = "card";
  const h = document.createElement("h3");
  h.textContent = "Compute Average";
  const row = document.createElement("div");
  row.className = "form-grid";
  const input = document.createElement("input");
  input.id = "computeJobId";
  input.placeholder = "Job ID";
  const actions = document.createElement("div");
  actions.className = "form-actions";
  actions.append(button("Compute", async () => {
    const id = input.value.trim();
    if (!id) return;
    try {
      const data = await apiJson(`/he/glucose/${encodeURIComponent(id)}/compute-average`, { method: "POST" });
      $("computeOutput").textContent = JSON.stringify(data, null, 2);
      $("heLookupJobId").value = id;
    } catch (error) { showToast(error.message); }
  }, "primary"));
  row.append(input, actions);
  card.append(h, row, outputBox("computeOutput"));
  return card;
}

function buildHELookupCard() {
  const card = document.createElement("div");
  card.className = "card";
  const h = document.createElement("h3");
  h.textContent = "Job";
  const row = document.createElement("div");
  row.className = "form-grid";
  const input = document.createElement("input");
  input.id = "heLookupJobId";
  input.placeholder = "Job ID";
  const actions = document.createElement("div");
  actions.className = "form-actions";
  actions.append(
    button("Ledger", () => loadHE("ledger")),
    button("History", () => loadHE("history")),
  );
  row.append(input, actions);
  card.append(h, row, outputBox("heLookupOutput"));
  return card;
}

function buildDicomSegmentInspectorCard() {
  const card = document.createElement("div");
  card.className = "card";
  const h = document.createElement("h3");
  h.textContent = "DICOM SEG Inspector";
  const row = document.createElement("div");
  row.className = "form-grid";
  const input = document.createElement("input");
  input.id = "dicomSegDatasetId";
  input.placeholder = "DICOM SEG Dataset ID";
  const actions = document.createElement("div");
  actions.className = "form-actions";
  actions.append(button("List Segments", async () => {
    const id = input.value.trim();
    if (!id) return;
    try {
      const data = await apiJson(
        `/he/dicom/segments/${encodeURIComponent(id)}`,
      );
      $("dicomSegOutput").textContent = JSON.stringify(data, null, 2);
      const target = document.querySelector(
        'form input[name="segmentation_dataset_id"]',
      );
      if (target) target.value = id;
    } catch (error) {
      showToast(error.message);
    }
  }, "primary"));
  row.append(input, actions);
  card.append(h, row, outputBox("dicomSegOutput"));
  return card;
}


function buildDicomEncryptCard() {
  const card = document.createElement("div");
  card.className = "card";
  const h = document.createElement("h3");
  h.textContent = "DICOM HE Encrypt";
  const form = document.createElement("form");
  form.className = "form-grid";
  form.innerHTML = `
    <input name="dataset_id" placeholder="DICOM Dataset ID" required>
    <input name="request_id" placeholder="Approved Request ID" required>
    <select name="analysis" title="Analysis">
      <option value="MEAN">Mean</option>
      <option value="SUM">Sum</option>
      <option value="VARIANCE">Variance</option>
      <option value="STANDARD_DEVIATION">Standard Deviation</option>
      <option value="ENERGY">Energy</option>
      <option value="TOTAL_ENERGY">Total Energy</option>
      <option value="SECOND_MOMENT">Second Moment</option>
      <option value="ROOT_MEAN_SQUARED">Root Mean Squared</option>
      <option value="SKEWNESS">Skewness</option>
      <option value="KURTOSIS">Kurtosis</option>
      <option value="CENTRAL_MOMENT_3">Central Moment 3</option>
      <option value="CENTRAL_MOMENT_4">Central Moment 4</option>
    </select>
    <select name="he_mode" title="HE mode">
      <option value="RAW_VOXELS">Raw Voxels (CKKS)</option>
      <option value="BLOCK_STATS">Block Stats (large volume)</option>
    </select>
    <select name="scope" title="Scope">
      <option value="WHOLE_VOLUME">Whole Volume</option>
      <option value="SLICE">Single Slice</option>
      <option value="ROI_BOX">ROI Box</option>
      <option value="DICOM_SEG">DICOM SEG Mask</option>
    </select>
    <input name="slice_index" type="number" min="0" placeholder="Slice index (SLICE only)">
    <input name="slice_start" type="number" min="0" placeholder="ROI slice start">
    <input name="slice_end" type="number" min="1" placeholder="ROI slice end (exclusive)">
    <input name="row_start" type="number" min="0" placeholder="ROI row start">
    <input name="row_end" type="number" min="1" placeholder="ROI row end (exclusive)">
    <input name="col_start" type="number" min="0" placeholder="ROI col start">
    <input name="col_end" type="number" min="1" placeholder="ROI col end (exclusive)">
    <input name="segmentation_dataset_id" placeholder="DICOM SEG Dataset ID (DICOM_SEG only)">
    <input name="segmentation_request_id" placeholder="Approved SEG Request ID (DICOM_SEG only)">
    <input name="segment_number" type="number" min="1" placeholder="Segment number (DICOM_SEG only)">
    <div class="full hint">DICOM SEG selects a hospital-side binary segment, then encrypts only its source CT/MR voxels. Raw-voxel mode limit: 262,144 selected voxels; use Block Stats for larger segments.</div>
    <div class="full form-actions"><button class="primary" type="submit">Encrypt DICOM</button></div>
  `;
  const out = outputBox("dicomEncryptOutput");

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const fd = new FormData(form);
    const scope = String(fd.get("scope"));
    const payload = {
      dataset_id: String(fd.get("dataset_id")).trim(),
      request_id: String(fd.get("request_id")).trim(),
      analysis: String(fd.get("analysis")),
      he_mode: String(fd.get("he_mode")),
      scope,
    };

    try {
      if (scope === "SLICE") {
        const raw = String(fd.get("slice_index") || "").trim();
        if (!raw) throw new Error("Slice index is required for SLICE scope");
        payload.slice_index = Number(raw);
      }

      if (scope === "ROI_BOX") {
        const names = [
          "slice_start", "slice_end",
          "row_start", "row_end",
          "col_start", "col_end",
        ];
        const roi = {};
        for (const name of names) {
          const raw = String(fd.get(name) || "").trim();
          if (!raw) throw new Error("All ROI box values are required");
          roi[name] = Number(raw);
        }
        payload.roi_box = roi;
      }

      if (scope === "DICOM_SEG") {
        const segId = String(fd.get("segmentation_dataset_id") || "").trim();
        const segRequestId = String(fd.get("segmentation_request_id") || "").trim();
        const segmentRaw = String(fd.get("segment_number") || "").trim();
        if (!segId) throw new Error("DICOM SEG Dataset ID is required");
        if (!segRequestId) throw new Error("Approved SEG Request ID is required");
        if (!segmentRaw) throw new Error("Segment number is required");
        payload.segmentation_dataset_id = segId;
        payload.segmentation_request_id = segRequestId;
        payload.segment_number = Number(segmentRaw);
      }

      const data = await apiJson("/he/dicom/encrypt", {
        method: "POST",
        ...jsonBody(payload),
      });
      out.textContent = JSON.stringify(data, null, 2);

      if (data.job_id) {
        const decrypt = $("dicomDecryptJobId");
        const lookup = $("dicomHeLookupJobId");
        if (decrypt) decrypt.value = data.job_id;
        if (lookup) lookup.value = data.job_id;
      }
      showToast("DICOM encrypted");
    } catch (error) {
      showToast(error.message);
    }
  });

  card.append(h, form, out);
  return card;
}

function buildDicomDecryptCard() {
  const card = document.createElement("div");
  card.className = "card";
  const h = document.createElement("h3");
  h.textContent = "DICOM HE Decrypt Result";
  const row = document.createElement("div");
  row.className = "form-grid";
  const input = document.createElement("input");
  input.id = "dicomDecryptJobId";
  input.placeholder = "DICOM HE Job ID";
  const actions = document.createElement("div");
  actions.className = "form-actions";
  actions.append(button("Decrypt", async () => {
    const id = input.value.trim();
    if (!id) return;
    try {
      const data = await apiJson(`/he/dicom/${encodeURIComponent(id)}/decrypt`, {
        method: "POST",
      });
      $("dicomDecryptOutput").textContent = JSON.stringify(data, null, 2);
      const lookup = $("dicomHeLookupJobId");
      if (lookup) lookup.value = id;
    } catch (error) {
      showToast(error.message);
    }
  }, "primary"));
  row.append(input, actions);
  card.append(h, row, outputBox("dicomDecryptOutput"));
  return card;
}

function buildDicomComputeCard() {
  const card = document.createElement("div");
  card.className = "card";
  const h = document.createElement("h3");
  h.textContent = "DICOM HE Compute";
  const row = document.createElement("div");
  row.className = "form-grid";
  const input = document.createElement("input");
  input.id = "dicomComputeJobId";
  input.placeholder = "DICOM HE Job ID";
  const actions = document.createElement("div");
  actions.className = "form-actions";
  actions.append(button("Compute", async () => {
    const id = input.value.trim();
    if (!id) return;
    try {
      const data = await apiJson(`/he/dicom/${encodeURIComponent(id)}/compute`, {
        method: "POST",
      });
      $("dicomComputeOutput").textContent = JSON.stringify(data, null, 2);
      const lookup = $("dicomHeLookupJobId");
      if (lookup) lookup.value = id;
    } catch (error) {
      showToast(error.message);
    }
  }, "primary"));
  row.append(input, actions);
  card.append(h, row, outputBox("dicomComputeOutput"));
  return card;
}

function buildDicomHELookupCard() {
  const card = document.createElement("div");
  card.className = "card";
  const h = document.createElement("h3");
  h.textContent = "DICOM HE Job";
  const row = document.createElement("div");
  row.className = "form-grid";
  const input = document.createElement("input");
  input.id = "dicomHeLookupJobId";
  input.placeholder = "DICOM HE Job ID";
  const actions = document.createElement("div");
  actions.className = "form-actions";
  actions.append(
    button("Ledger", () => loadDicomHE("ledger")),
    button("History", () => loadDicomHE("history")),
  );
  row.append(input, actions);
  card.append(h, row, outputBox("dicomHeLookupOutput"));
  return card;
}

async function loadDicomHE(kind) {
  const id = $("dicomHeLookupJobId").value.trim();
  if (!id) return;
  try {
    const data = await apiJson(`/he/dicom/${encodeURIComponent(id)}/${kind}`);
    $("dicomHeLookupOutput").textContent = JSON.stringify(data, null, 2);
  } catch (error) {
    showToast(error.message);
  }
}


async function loadHE(kind) {
  const id = $("heLookupJobId").value.trim();
  if (!id) return;
  try {
    const data = await apiJson(`/he/glucose/${encodeURIComponent(id)}/${kind}`);
    $("heLookupOutput").textContent = JSON.stringify(data, null, 2);
  } catch (error) {
    showToast(error.message);
  }
}

$("loginButton").addEventListener("click", login);
$("tokenInput").addEventListener("keydown", (event) => {
  if (event.key === "Enter") login();
});
$("logoutButton").addEventListener("click", logout);

document.querySelectorAll(".nav-button").forEach((buttonEl) => {
  buttonEl.addEventListener("click", () => setView(buttonEl.dataset.view));
});

(async () => {
  if (!state.token) return;
  try {
    const me = await apiJson("/auth/me");
    state.role = me.role;
    sessionStorage.setItem("medical_role", state.role);
    openApp();
  } catch (_) {
    logout();
  }
})();
