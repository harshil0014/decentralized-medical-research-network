(() => {
  function token() {
    return sessionStorage.getItem("medical_token") || "";
  }

  function isHospital() {
    const badge = document.getElementById("roleBadge");
    return (badge?.textContent || "").trim().toLowerCase() === "hospital";
  }

  function closePreview() {
    document.getElementById("datasetPreviewOverlay")?.remove();
  }

  function renderPreview(data) {
    closePreview();

    const overlay = document.createElement("div");
    overlay.id = "datasetPreviewOverlay";
    Object.assign(overlay.style, {
      position: "fixed",
      inset: "0",
      background: "rgba(0,0,0,0.45)",
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      zIndex: "9999",
      padding: "24px",
    });

    const panel = document.createElement("div");
    Object.assign(panel.style, {
      background: "white",
      width: "min(1000px, 95vw)",
      maxHeight: "85vh",
      overflow: "auto",
      borderRadius: "12px",
      padding: "20px",
      boxShadow: "0 20px 60px rgba(0,0,0,0.25)",
    });

    const header = document.createElement("div");
    Object.assign(header.style, {
      display: "flex",
      justifyContent: "space-between",
      alignItems: "center",
      gap: "16px",
      marginBottom: "16px",
    });

    const title = document.createElement("h3");
    title.textContent = `CSV Preview — ${data.datasetId}`;
    title.style.margin = "0";

    const close = document.createElement("button");
    close.textContent = "Close";
    close.addEventListener("click", closePreview);

    header.append(title, close);
    panel.append(header);

    const wrap = document.createElement("div");
    wrap.style.overflowX = "auto";

    const table = document.createElement("table");
    table.style.width = "100%";

    const thead = document.createElement("thead");
    const headRow = document.createElement("tr");
    for (const column of data.columns || []) {
      const th = document.createElement("th");
      th.textContent = column;
      headRow.append(th);
    }
    thead.append(headRow);
    table.append(thead);

    const tbody = document.createElement("tbody");
    for (const row of data.rows || []) {
      const tr = document.createElement("tr");
      for (const column of data.columns || []) {
        const td = document.createElement("td");
        td.textContent = row[column] ?? "";
        tr.append(td);
      }
      tbody.append(tr);
    }
    table.append(tbody);

    wrap.append(table);
    panel.append(wrap);

    if (data.truncated) {
      const note = document.createElement("div");
      note.textContent = `Showing first ${data.previewLimit || 50} rows.`;
      note.style.marginTop = "12px";
      note.style.opacity = "0.7";
      panel.append(note);
    }

    overlay.append(panel);
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) closePreview();
    });
    document.body.append(overlay);
  }

  async function previewDataset(datasetId) {
    try {
      const response = await fetch(
        `/datasets/${encodeURIComponent(datasetId)}/preview`,
        {
          headers: {
            Authorization: `Bearer ${token()}`,
          },
        },
      );

      if (!response.ok) {
        let message = `${response.status}`;
        try {
          const data = await response.json();
          message = data.detail || message;
        } catch (_) {}
        throw new Error(message);
      }

      renderPreview(await response.json());
    } catch (error) {
      alert(`Preview failed: ${error.message}`);
    }
  }

  function installViewButtons() {
    if (!isHospital()) return;

    const table = document.getElementById("datasetsTable");
    if (!table) return;

    for (const row of table.querySelectorAll("tbody tr")) {
      const cells = row.querySelectorAll("td");
      if (cells.length < 5) continue;

      const datasetId = cells[0].textContent.trim();
      const type = cells[1].textContent.trim().toUpperCase();
      const actions = cells[4].querySelector(".actions") || cells[4];

      if (!datasetId || actions.querySelector("[data-csv-preview]")) continue;
      if (!["CSV", "LAB_CSV", "NUMERIC_CSV"].includes(type)) continue;

      const view = document.createElement("button");
      view.textContent = "View";
      view.type = "button";
      view.dataset.csvPreview = "true";
      view.addEventListener("click", () => previewDataset(datasetId));
      actions.prepend(view);
    }
  }

  const observer = new MutationObserver(() => installViewButtons());

  window.addEventListener("DOMContentLoaded", () => {
    observer.observe(document.body, { childList: true, subtree: true });
    installViewButtons();
  });
})();
