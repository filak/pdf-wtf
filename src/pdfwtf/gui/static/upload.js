const documentInput = document.getElementById("document");
const documentFilename = document.getElementById("document-filename");
const documentDropZone = document.getElementById("document-drop-zone");

function updateDocumentFilename() {
  documentFilename.textContent = documentInput.files[0]?.name || documentFilename.dataset.empty;
}

documentInput.addEventListener("change", updateDocumentFilename);
documentInput.form.addEventListener("reset", () => {
  setTimeout(updateDocumentFilename, 0);
});
documentDropZone.addEventListener("dragenter", (event) => {
  if (event.dataTransfer.types.includes("Files")) {
    event.preventDefault();
    documentDropZone.classList.add("is-dragging");
  }
});
documentDropZone.addEventListener("dragover", (event) => {
  if (event.dataTransfer.types.includes("Files")) {
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
    documentDropZone.classList.add("is-dragging");
  }
});
documentDropZone.addEventListener("dragleave", (event) => {
  if (!documentDropZone.contains(event.relatedTarget)) {
    documentDropZone.classList.remove("is-dragging");
  }
});
documentDropZone.addEventListener("drop", (event) => {
  documentDropZone.classList.remove("is-dragging");
  if (!event.dataTransfer.types.includes("Files")) return;

  event.preventDefault();
  const file = event.dataTransfer.files[0];
  if (!file) return;

  const transfer = new DataTransfer();
  transfer.items.add(file);
  documentInput.files = transfer.files;
  documentInput.dispatchEvent(new Event("change", { bubbles: true }));
});
updateDocumentFilename();

document.body.addEventListener("htmx:beforeSwap", (event) => {
  if ([400, 409, 413, 500].includes(event.detail.xhr.status)) {
    if (event.detail.target.id === "uploaded-files") {
      event.detail.target = document.getElementById("upload-error");
    }
    event.detail.shouldSwap = true;
    event.detail.isError = false;
    event.detail.swapOverride = "innerHTML";
  }
});

documentInput.form.addEventListener("htmx:afterRequest", (event) => {
  if (event.detail.successful) {
    document.getElementById("upload-error").replaceChildren();
    documentInput.form.reset();
  }
});
