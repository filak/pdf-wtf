const documentInput = document.getElementById("document");
const documentFilename = document.getElementById("document-filename");

function updateDocumentFilename() {
  documentFilename.textContent = documentInput.files[0]?.name || documentFilename.dataset.empty;
}

documentInput.addEventListener("change", updateDocumentFilename);
documentInput.form.addEventListener("reset", () => {
  setTimeout(updateDocumentFilename, 0);
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
