(() => {
  function token() {
    return sessionStorage.getItem("medical_token") || "";
  }

  function role() {
    const badge = document.getElementById("roleBadge");
    return (badge?.textContent || "").trim().toLowerCase();
  }

  async function postJson(path) {
    const response = await fetch(path, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token()}`,
      },
    });

    if (!response.ok) {
      let message = `${response.status}`;
      try {
        const data = await response.json();
        message = typeof data.detail === "string"
          ? data.detail
          : JSON.stringify(data.detail || data);
      } catch (_) {}
      throw new Error(message);
    }

    return response.json();
  }

  function makeOutput() {
    const pre = document.createElement("pre");
    pre.className = "output";
    return pre;
  }

  function buildResearcherSumCard() {
    const card = document.createElement("div");
    card.className = "card";
    card.dataset.extraHeSum = "researcher";

    const title = document.createElement("h3");
    title.textContent = "Compute Sum";

    const row = document.createElement("div");
    row.className = "form-grid";

    const input = document.createElement("input");
    input.placeholder = "Job ID";

    const actions = document.createElement("div");
    actions.className = "form-actions";

    const button = document.createElement("button");
    button.type = "button";
    button.className = "primary";
    button.textContent = "Compute Sum";

    const output = makeOutput();

    button.addEventListener("click", async () => {
      const id = input.value.trim();
      if (!id) return;

      button.disabled = true;
      try {
        const data = await postJson(`/he/sum/${encodeURIComponent(id)}/compute`);
        output.textContent = JSON.stringify(data, null, 2);
      } catch (error) {
        output.textContent = `Error: ${error.message}`;
      } finally {
        button.disabled = false;
      }
    });

    actions.append(button);
    row.append(input, actions);
    card.append(title, row, output);
    return card;
  }

  function buildHospitalSumCard() {
    const card = document.createElement("div");
    card.className = "card";
    card.dataset.extraHeSum = "hospital";

    const title = document.createElement("h3");
    title.textContent = "Decrypt Sum Result";

    const row = document.createElement("div");
    row.className = "form-grid";

    const input = document.createElement("input");
    input.placeholder = "Job ID";

    const actions = document.createElement("div");
    actions.className = "form-actions";

    const button = document.createElement("button");
    button.type = "button";
    button.className = "primary";
    button.textContent = "Decrypt Sum";

    const output = makeOutput();

    button.addEventListener("click", async () => {
      const id = input.value.trim();
      if (!id) return;

      button.disabled = true;
      try {
        const data = await postJson(`/he/sum/${encodeURIComponent(id)}/decrypt`);
        output.textContent = JSON.stringify(data, null, 2);
      } catch (error) {
        output.textContent = `Error: ${error.message}`;
      } finally {
        button.disabled = false;
      }
    });

    actions.append(button);
    row.append(input, actions);
    card.append(title, row, output);
    return card;
  }

  function installExtraHE() {
    const root = document.getElementById("heView");
    if (!root) return;

    const currentRole = role();

    if (
      currentRole === "researcher" &&
      !root.querySelector('[data-extra-he-sum="researcher"]')
    ) {
      root.append(buildResearcherSumCard());
    }

    if (
      currentRole === "hospital" &&
      !root.querySelector('[data-extra-he-sum="hospital"]')
    ) {
      root.append(buildHospitalSumCard());
    }
  }

  const observer = new MutationObserver(() => installExtraHE());

  window.addEventListener("DOMContentLoaded", () => {
    observer.observe(document.body, { childList: true, subtree: true });
    installExtraHE();
  });
})();
