const confirmationElement = document.getElementById("delete-confirmation");
const confirmationModal = new window.bootstrap.Modal(confirmationElement);
let pendingConfirmation = null;
let confirmationTrigger = null;

document.addEventListener("htmx:confirm", (event) => {
  if (!event.detail.question || event.detail.verb !== "delete") return;
  event.preventDefault();
  if (pendingConfirmation) return;
  pendingConfirmation = event.detail.issueRequest;
  confirmationTrigger = event.detail.elt;
  document.getElementById("delete-confirmation-message").textContent = event.detail.question;
  confirmationModal.show();
});

confirmationElement.addEventListener("shown.bs.modal", () => {
  document.getElementById("delete-confirmation-cancel").focus();
});

confirmationElement.addEventListener("hide.bs.modal", () => {
  // Bootstrap sets aria-hidden on hide; a focused descendant triggers a browser warning.
  if (confirmationElement.contains(document.activeElement)) document.activeElement.blur();
});

confirmationElement.addEventListener("hidden.bs.modal", () => {
  pendingConfirmation = null;
  if (confirmationTrigger?.isConnected) confirmationTrigger.focus();
  confirmationTrigger = null;
});

document.getElementById("delete-confirmation-submit").addEventListener("click", () => {
  const issueRequest = pendingConfirmation;
  pendingConfirmation = null;
  confirmationModal.hide();
  if (issueRequest && confirmationTrigger?.isConnected) issueRequest(true);
});
