const exportDialog = document.getElementById("export-options");
const exportForm = document.getElementById("export-form");
const exportFeedback = document.getElementById("export-feedback");
let exportUrl;
let exportStatusUrl;
let exportGeneration = 0;

function setExportBusy(busy) {
  exportForm.querySelectorAll('input, button[type="submit"]').forEach((control) => {
    control.disabled = busy;
  });
}

function selectedLlmTasks() {
  return Array.from(exportForm.querySelectorAll('input[name="llm_task"]:checked'), (input) => input.value);
}

exportForm.addEventListener("change", (event) => {
  // LLM tasks run after unit HTML generation, so they require get-html.
  if (event.target.name === "llm_task" && event.target.checked) {
    exportForm.elements.get_html.checked = true;
  } else if (event.target.name === "get_html" && !event.target.checked) {
    exportForm.querySelectorAll('input[name="llm_task"]').forEach((input) => {
      input.checked = false;
    });
  }
});

function showExportError(message) {
  exportFeedback.className = "mt-3 text-danger";
  exportFeedback.textContent = message || exportDialog.dataset.error;
}

async function checkExport(statusUrl, generation) {
  while (generation === exportGeneration) {
    const response = await fetch(statusUrl, { cache: "no-store", signal: AbortSignal.timeout(10000) });
    if (generation !== exportGeneration) return;
    if (response.status === 404) return;
    if (!response.ok) throw new Error(exportDialog.dataset.error);
    const result = await response.json();
    if (generation !== exportGeneration) return;
    if (result.state === "failed") throw new Error(result.error);
    if (result.state === "complete") {
      exportFeedback.className = result.warning ? "mt-3 alert alert-warning" : "mt-3";
      exportFeedback.textContent = result.warning || exportDialog.dataset.complete;
      if (result.warning_details?.length) {
        const list = document.createElement("ul");
        list.className = "mb-2 mt-2 small text-break";
        result.warning_details.forEach((detail) => {
          const item = document.createElement("li");
          item.textContent = detail;
          list.append(item);
        });
        exportFeedback.append(list);
      }
      const link = document.createElement("a");
      link.className = "btn btn-success ms-2";
      link.href = result.download_url;
      link.textContent = exportDialog.dataset.download;
      exportFeedback.append(link);
      return;
    }
    setExportBusy(true);
    exportFeedback.className = "mt-3";
    exportFeedback.textContent = exportDialog.dataset.starting;
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
}

exportDialog.addEventListener("show.bs.modal", async (event) => {
  const generation = ++exportGeneration;
  exportUrl = undefined;
  exportStatusUrl = undefined;
  try {
    exportForm.reset();
    exportFeedback.replaceChildren();
    exportFeedback.className = "mt-3";
    document.getElementById("export-source-filename").textContent = "";
    setExportBusy(true);
    const trigger = event.relatedTarget;
    const sourceLink = trigger instanceof Element
      ? trigger.closest(".upload-row")?.querySelector('a[target="_blank"]') : null;
    // The Review page has no upload row, so its trigger carries the filename.
    const sourceFilename = trigger instanceof Element
      ? trigger.dataset.sourceFilename || sourceLink?.textContent : undefined;
    if (!sourceFilename || !trigger.dataset.exportUrl || !trigger.dataset.exportStatusUrl) {
      throw new Error(exportDialog.dataset.error);
    }
    document.getElementById("export-source-filename").textContent = sourceFilename;
    exportUrl = trigger.dataset.exportUrl;
    exportStatusUrl = trigger.dataset.exportStatusUrl;
    await checkExport(exportStatusUrl, generation);
  } catch (error) {
    if (generation === exportGeneration) showExportError(error.message);
  } finally {
    if (generation === exportGeneration) setExportBusy(!exportUrl);
  }
});

exportDialog.addEventListener("hide.bs.modal", () => {
  // Bootstrap sets aria-hidden on hide; a focused descendant triggers a browser warning.
  if (exportDialog.contains(document.activeElement)) document.activeElement.blur();
});

exportDialog.addEventListener("hidden.bs.modal", () => {
  exportGeneration += 1;
});

exportForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!exportUrl) return;
  const generation = exportGeneration;
  const options = {
    get_html: exportForm.elements.get_html.checked,
    get_meta: exportForm.elements.get_meta.checked,
    get_img: exportForm.elements.get_img.checked,
    include_source: exportForm.elements.include_source.checked,
    debug: exportForm.elements.debug.checked,
  };
  const llmTasks = selectedLlmTasks();
  if (llmTasks.length) {
    options.get_html = true;
    options.llm_tasks = llmTasks;
  }
  setExportBusy(true);
  exportFeedback.className = "mt-3";
  exportFeedback.textContent = exportDialog.dataset.starting;
  try {
    const response = await fetch(exportUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": document.querySelector('meta[name="csrf-token"]')?.content || "",
      },
      body: JSON.stringify(options),
      signal: AbortSignal.timeout(10000),
    });
    if (generation !== exportGeneration) return;
    let result;
    try {
      result = await response.json();
    } catch {
      throw new Error(exportDialog.dataset.error);
    }
    if (!response.ok) throw new Error(result.error || exportDialog.dataset.error);
    await checkExport(result.status_url, generation);
  } catch (error) {
    if (generation === exportGeneration) showExportError(error.message);
  } finally {
    if (generation === exportGeneration) setExportBusy(false);
  }
});
