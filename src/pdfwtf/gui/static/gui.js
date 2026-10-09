import * as pdfjsLib from "./vendor/pdfjs/pdf.min.mjs";

pdfjsLib.GlobalWorkerOptions.workerSrc = new URL(
  "./vendor/pdfjs/pdf.worker.min.mjs",
  import.meta.url,
).href;

const app = document.querySelector("#review-app");
if (app) {
  const analysis = JSON.parse(document.querySelector("#analysis-data").textContent);
  const messages = JSON.parse(document.querySelector("#gui-messages").textContent);
  const unitTypes = JSON.parse(document.querySelector("#unit-types").textContent);
  const unitTypeLabels = JSON.parse(
    document.querySelector("#unit-type-labels").textContent,
  );
  const state = {
    document: null,
    currentPage: 1,
    scale: 1,
    previewTask: null,
    thumbnailTasks: new Set(),
    thumbnailQueue: [],
    activeThumbnails: 0,
    maxThumbnailConcurrency: 3,
    units: analysis.units.map((unit) => ({
      id: unit.id,
      title: unit.title,
      type: unit.type,
      input_pages: { ...unit.input_pages },
      selected: true,
      boundary_status: "confirmed",
    })),
  };

  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content;
  const previewCanvas = document.querySelector("#preview-canvas");
  const previewInput = document.querySelector("#preview-page");
  const pageCount = document.querySelector("#page-count");

  function format(template, values) {
    return Object.entries(values).reduce(
      (text, [key, value]) => text.replace(`%(${key})s`, value),
      template,
    );
  }

  function showMessage(text, kind = "danger") {
    const target = document.querySelector("#plan-message");
    target.replaceChildren();
    const alert = document.createElement("div");
    alert.className = `alert alert-${kind}`;
    alert.textContent = text;
    target.append(alert);
  }

  async function renderPreview() {
    if (!state.document) return;
    state.previewTask?.cancel();
    const page = await state.document.getPage(state.currentPage);
    const viewport = page.getViewport({ scale: state.scale });
    const ratio = window.devicePixelRatio || 1;
    previewCanvas.width = Math.floor(viewport.width * ratio);
    previewCanvas.height = Math.floor(viewport.height * ratio);
    previewCanvas.style.width = `${viewport.width}px`;
    previewCanvas.style.height = `${viewport.height}px`;
    state.previewTask = page.render({
      canvasContext: previewCanvas.getContext("2d"),
      viewport,
      transform: ratio === 1 ? null : [ratio, 0, 0, ratio, 0, 0],
    });
    try {
      await state.previewTask.promise;
    } catch (error) {
      if (error?.name !== "RenderingCancelledException") throw error;
    } finally {
      page.cleanup();
    }
    previewInput.value = state.currentPage;
    document.querySelector("#zoom-level").value = `${Math.round(state.scale * 100)}%`;
    document.querySelectorAll(".thumbnail-button").forEach((button) => {
      button.classList.toggle("active", Number(button.dataset.page) === state.currentPage);
    });
  }

  function selectPage(pageNumber) {
    state.currentPage = Math.max(1, Math.min(state.document.numPages, pageNumber));
    renderPreview().catch(() => showMessage(messages.loading_error));
  }

  async function renderThumbnail(canvas, pageNumber) {
    state.activeThumbnails += 1;
    const page = await state.document.getPage(pageNumber);
    const natural = page.getViewport({ scale: 1 });
    const viewport = page.getViewport({ scale: 150 / natural.width });
    canvas.width = Math.ceil(viewport.width);
    canvas.height = Math.ceil(viewport.height);
    const task = page.render({ canvasContext: canvas.getContext("2d"), viewport });
    state.thumbnailTasks.add(task);
    try {
      await task.promise;
      canvas.previousElementSibling?.remove();
    } catch (error) {
      if (error?.name !== "RenderingCancelledException") throw error;
    } finally {
      state.thumbnailTasks.delete(task);
      state.activeThumbnails -= 1;
      page.cleanup();
      drainThumbnailQueue();
    }
  }

  function drainThumbnailQueue() {
    while (
      state.activeThumbnails < state.maxThumbnailConcurrency &&
      state.thumbnailQueue.length
    ) {
      const item = state.thumbnailQueue.shift();
      renderThumbnail(item.canvas, item.pageNumber).catch(() => {
        item.canvas.closest("button").disabled = true;
      });
    }
  }

  function buildThumbnails() {
    const container = document.querySelector("#thumbnails");
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          const button = entry.target;
          observer.unobserve(button);
          state.thumbnailQueue.push({
            canvas: button.querySelector("canvas"),
            pageNumber: Number(button.dataset.page),
          });
        }
        drainThumbnailQueue();
      },
      { root: container, rootMargin: "150px" },
    );
    for (let pageNumber = 1; pageNumber <= state.document.numPages; pageNumber += 1) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "thumbnail-button mb-2";
      button.dataset.page = pageNumber;
      button.setAttribute("aria-label", format(messages.view_page, { page: pageNumber }));
      button.innerHTML = `<div class="thumbnail-placeholder"></div><canvas></canvas><span>${pageNumber}</span>`;
      button.addEventListener("click", () => selectPage(pageNumber));
      container.append(button);
      observer.observe(button);
    }
  }

  function option(value, selected) {
    const item = document.createElement("option");
    item.value = value;
    item.textContent = unitTypeLabels[value] || value;
    item.selected = value === selected;
    return item;
  }

  function unitField(label, name, value, type = "text") {
    const column = document.createElement("div");
    column.className = name === "title" ? "col-12" : "col-sm-6";
    const labelElement = document.createElement("label");
    labelElement.className = "form-label small mb-1";
    labelElement.textContent = label;
    const input = document.createElement("input");
    input.className = "form-control form-control-sm";
    input.type = type;
    input.name = name;
    input.value = value;
    if (type === "number") input.min = "1";
    labelElement.append(input);
    column.append(labelElement);
    return column;
  }

  function renderUnits() {
    const container = document.querySelector("#units");
    container.replaceChildren();
    state.units.forEach((unit, index) => {
      const card = document.createElement("article");
      card.className = `unit-card border rounded p-3 mb-3${unit.selected ? "" : " excluded"}`;
      card.dataset.index = index;

      const header = document.createElement("div");
      header.className = "d-flex align-items-center gap-2 mb-2";
      const merge = document.createElement("input");
      merge.type = "checkbox";
      merge.className = "form-check-input merge-unit";
      merge.setAttribute("aria-label", messages.select_merge);
      const heading = document.createElement("strong");
      heading.textContent = unit.id;
      const included = document.createElement("button");
      included.type = "button";
      included.className = `btn btn-sm ms-auto ${unit.selected ? "btn-outline-success" : "btn-outline-secondary"}`;
      included.textContent = unit.selected ? messages.included : messages.excluded;
      included.setAttribute("aria-label", messages.toggle_unit);
      included.addEventListener("click", () => {
        unit.selected = !unit.selected;
        renderUnits();
      });
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "btn btn-sm btn-outline-danger";
      remove.textContent = messages.remove;
      remove.addEventListener("click", () => {
        if (window.confirm(messages.confirm_delete)) {
          state.units.splice(index, 1);
          renderUnits();
        }
      });
      header.append(merge, heading, included, remove);

      const fields = document.createElement("div");
      fields.className = "row g-2";
      fields.append(
        unitField(messages.unit_id, "id", unit.id),
        unitField(messages.title, "title", unit.title),
      );
      const typeColumn = document.createElement("div");
      typeColumn.className = "col-sm-6";
      const typeLabel = document.createElement("label");
      typeLabel.className = "form-label small mb-1";
      typeLabel.textContent = messages.type;
      const typeSelect = document.createElement("select");
      typeSelect.className = "form-select form-select-sm";
      typeSelect.name = "type";
      unitTypes.forEach((value) => typeSelect.append(option(value, unit.type)));
      typeLabel.append(typeSelect);
      typeColumn.append(typeLabel);
      fields.append(
        typeColumn,
        unitField(messages.start_page, "start", unit.input_pages.start, "number"),
        unitField(messages.end_page, "end", unit.input_pages.end, "number"),
      );
      card.append(header, fields);
      container.append(card);
    });
  }

  function readUnitCards() {
    document.querySelectorAll(".unit-card").forEach((card) => {
      const unit = state.units[Number(card.dataset.index)];
      unit.id = card.querySelector('[name="id"]').value.trim();
      unit.title = card.querySelector('[name="title"]').value.trim();
      unit.type = card.querySelector('[name="type"]').value;
      unit.input_pages.start = Number(card.querySelector('[name="start"]').value);
      unit.input_pages.end = Number(card.querySelector('[name="end"]').value);
    });
  }

  function newUnitId() {
    let number = 1;
    let candidate;
    do {
      candidate = `unit-${String(number).padStart(3, "0")}`;
      number += 1;
    } while (state.units.some((unit) => unit.id === candidate));
    return candidate;
  }

  function addUnit() {
    readUnitCards();
    state.units.push({
      id: newUnitId(),
      title: messages.add_title,
      type: "unknown",
      input_pages: { start: 1, end: 1 },
      selected: true,
      boundary_status: "confirmed",
    });
    renderUnits();
  }

  function mergeUnits() {
    readUnitCards();
    const selected = [...document.querySelectorAll(".merge-unit:checked")]
      .map((control) => Number(control.closest(".unit-card").dataset.index))
      .sort((first, second) => first - second);
    const units = selected.map((index) => state.units[index]);
    const ordered = [...units].sort(
      (first, second) => first.input_pages.start - second.input_pages.start,
    );
    const adjacent = ordered.every(
      (unit, index) => index === 0 || ordered[index - 1].input_pages.end + 1 === unit.input_pages.start,
    );
    if (units.length < 2 || !adjacent) {
      showMessage(messages.invalid_merge);
      return;
    }
    const merged = {
      id: ordered[0].id,
      title: ordered[0].title || messages.merge_title,
      type: ordered[0].type,
      input_pages: {
        start: ordered[0].input_pages.start,
        end: ordered.at(-1).input_pages.end,
      },
      selected: ordered.some((unit) => unit.selected),
      boundary_status: "confirmed",
    };
    state.units = state.units.filter((_unit, index) => !selected.includes(index));
    state.units.push(merged);
    state.units.sort((first, second) => first.input_pages.start - second.input_pages.start);
    renderUnits();
  }

  function buildPlan() {
    readUnitCards();
    const units = state.units.map((unit) => ({
      id: unit.id,
      title: unit.title,
      type: unit.type,
      selected: unit.selected,
      boundary_status: "confirmed",
      input_pages: { ...unit.input_pages },
    }));
    const pages = analysis.pages.map((page) => ({
      ...page,
      selected: true,
      unit_ids: units
        .filter(
          (unit) =>
            unit.input_pages.start <= page.input_page &&
            page.input_page <= unit.input_pages.end,
        )
        .map((unit) => unit.id),
    }));
    return {
      schema_version: analysis.schema_version,
      kind: "plan",
      source: analysis.source,
      document_type: analysis.document_type,
      pages,
      units,
    };
  }

  async function savePlan() {
    const button = document.querySelector("#save-plan");
    button.disabled = true;
    const oldText = button.textContent;
    button.textContent = messages.saving;
    try {
      const response = await fetch(app.dataset.saveUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": csrfToken,
        },
        body: JSON.stringify(buildPlan()),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.details || payload.error || messages.save_error);
      const target = document.querySelector("#plan-message");
      target.replaceChildren();
      const alert = document.createElement("div");
      alert.className = "alert alert-success";
      alert.textContent = payload.message;
      const link = document.createElement("a");
      link.className = "btn btn-sm btn-success ms-3";
      link.href = payload.download_url;
      link.textContent = payload.download_url ? "JSON" : "";
      alert.append(link);
      target.append(alert);
    } catch (error) {
      showMessage(error.message || messages.save_error);
    } finally {
      button.disabled = false;
      button.textContent = oldText;
    }
  }

  document.querySelector("#previous-page").addEventListener("click", () => selectPage(state.currentPage - 1));
  document.querySelector("#next-page").addEventListener("click", () => selectPage(state.currentPage + 1));
  previewInput.addEventListener("change", () => selectPage(Number(previewInput.value)));
  document.querySelector("#zoom-in").addEventListener("click", () => {
    state.scale = Math.min(3, state.scale + 0.25);
    renderPreview();
  });
  document.querySelector("#zoom-out").addEventListener("click", () => {
    state.scale = Math.max(0.5, state.scale - 0.25);
    renderPreview();
  });
  document.querySelector("#add-unit").addEventListener("click", addUnit);
  document.querySelector("#merge-units").addEventListener("click", mergeUnits);
  document.querySelector("#save-plan").addEventListener("click", savePlan);

  window.addEventListener("pagehide", () => {
    state.previewTask?.cancel();
    state.thumbnailTasks.forEach((task) => task.cancel());
    state.thumbnailQueue.length = 0;
    state.document?.destroy();
    state.document = null;
  });

  renderUnits();
  pdfjsLib.getDocument(app.dataset.documentUrl).promise
    .then((document) => {
      state.document = document;
      previewInput.max = document.numPages;
      pageCount.textContent = `/ ${document.numPages}`;
      buildThumbnails();
      return renderPreview();
    })
    .catch(() => showMessage(messages.loading_error));
}
