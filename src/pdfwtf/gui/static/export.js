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
      exportFeedback.className = "mt-3";
      exportFeedback.textContent = exportDialog.dataset.complete;
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
  const sourceLink = event.relatedTarget.closest(".upload-row").querySelector('a[target="_blank"]');
  document.getElementById("export-source-filename").textContent = sourceLink.textContent;
  exportUrl = event.relatedTarget.dataset.exportUrl;
  exportStatusUrl = event.relatedTarget.dataset.exportStatusUrl;
  const generation = ++exportGeneration;
  exportForm.reset();
  exportFeedback.replaceChildren();
  exportFeedback.className = "mt-3";
  setExportBusy(true);
  try {
    await checkExport(exportStatusUrl, generation);
  } catch (error) {
    if (generation === exportGeneration) showExportError(error.message);
  } finally {
    if (generation === exportGeneration) setExportBusy(false);
  }
});

exportDialog.addEventListener("hidden.bs.modal", () => {
  exportGeneration += 1;
});

exportForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const generation = exportGeneration;
  const options = {
    no_pdf_out: exportForm.elements.no_pdf_out.checked,
    get_html: exportForm.elements.get_html.checked,
    get_meta: exportForm.elements.get_meta.checked,
    include_source: exportForm.elements.include_source.checked,
    debug: exportForm.elements.debug.checked,
  };
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
