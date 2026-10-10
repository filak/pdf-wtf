import * as pdfjsLib from "./vendor/pdfjs/pdf.min.mjs";

pdfjsLib.GlobalWorkerOptions.workerSrc = new URL(
  "./vendor/pdfjs/pdf.worker.min.mjs",
  import.meta.url,
).href;

const app = document.querySelector("#review-app");
if (app) {
  const analysis = JSON.parse(document.querySelector("#analysis-data").textContent);
  const plan = JSON.parse(document.querySelector("#plan-data").textContent);
  const reviewData = plan || analysis;
  const messages = JSON.parse(document.querySelector("#gui-messages").textContent);
  const unitTypes = JSON.parse(document.querySelector("#unit-types").textContent);
  const unitTypeLabels = new Map(Object.entries(JSON.parse(
    document.querySelector("#unit-type-labels").textContent,
  )));
  const state = {
    document: null,
    currentPage: 1,
    activeUnitId: null,
    saving: false,
    scale: 1,
    previewTask: null,
    textLayer: null,
    textPromise: null,
    previewGeneration: 0,
    thumbnailTasks: new Set(),
    thumbnailQueue: [],
    activeThumbnails: 0,
    maxThumbnailConcurrency: 3,
    units: reviewData.units.map((unit) => ({
      id: unit.id,
      title: unit.title,
      type: unit.type,
      input_pages: { ...unit.input_pages },
      selected: unit.selected ?? true,
      boundary_status: unit.boundary_status || "confirmed",
    })),
  };

  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content;
  const previewContainer = document.querySelector("#preview-container");
  const previewPanel = document.querySelector(".preview-panel");
  const previewPageView = document.querySelector("#preview-page-view");
  const previewTextLayer = document.querySelector("#preview-text-layer");
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
    const generation = ++state.previewGeneration;
    const pageNumber = state.currentPage;
    const zoom = state.scale;
    const previousTask = state.previewTask;
    const previousText = state.textPromise;
    previousTask?.cancel();
    state.textLayer?.cancel();
    previewTextLayer.replaceChildren();
    await Promise.allSettled([previousTask?.promise, previousText]);
    if (generation !== state.previewGeneration || !state.document) return;
    const page = await state.document.getPage(pageNumber);
    if (generation !== state.previewGeneration) return;
    const padding = getComputedStyle(previewContainer);
    const availableWidth = previewContainer.clientWidth
      - parseFloat(padding.paddingLeft) - parseFloat(padding.paddingRight);
    const naturalViewport = page.getViewport({ scale: 1 });
    const scale = Math.max(1, availableWidth) / naturalViewport.width * zoom;
    const viewport = page.getViewport({ scale });
    const ratio = window.devicePixelRatio || 1;
    previewCanvas.width = Math.floor(viewport.width * ratio);
    previewCanvas.height = Math.floor(viewport.height * ratio);
    previewCanvas.style.width = `${viewport.width}px`;
    previewCanvas.style.height = `${viewport.height}px`;
    previewPageView.style.width = `${viewport.width}px`;
    previewPageView.style.height = `${viewport.height}px`;
    previewTextLayer.style.setProperty("--total-scale-factor", viewport.scale * viewport.userUnit);
    const renderTask = page.render({
      canvasContext: previewCanvas.getContext("2d"),
      viewport,
      transform: ratio === 1 ? null : [ratio, 0, 0, ratio, 0, 0],
    });
    state.previewTask = renderTask;
    state.textLayer = new pdfjsLib.TextLayer({
      textContentSource: page.streamTextContent(),
      container: previewTextLayer,
      viewport,
    });
    state.textPromise = state.textLayer.render();
    try {
      await Promise.all([renderTask.promise, state.textPromise]);
    } catch (error) {
      if (generation === state.previewGeneration) throw error;
      return;
    } finally {
      page.cleanup();
    }
    if (generation !== state.previewGeneration) return;
    previewInput.value = pageNumber;
    document.querySelector("#zoom-level").value = `${Math.round(scale * 100)}%`;
    document.querySelectorAll(".thumbnail-button").forEach((button) => {
      button.classList.toggle("active", Number(button.dataset.page) === pageNumber);
    });
  }

  function synchronizeUnit(scroll = false) {
    updateThumbnailInclusion();
    const cards = [...document.querySelectorAll(".unit-card")];
    const containsPage = (card) => Number(card.querySelector('[name="start"]').value) <= state.currentPage
      && state.currentPage <= Number(card.querySelector('[name="end"]').value);
    const preferred = cards.find((card) => state.units[Number(card.dataset.index)].id === state.activeUnitId);
    const chosen = preferred && containsPage(preferred) ? preferred : cards.find(containsPage);
    let activeCard = null;
    cards.forEach((card) => {
      const active = card === chosen;
      card.classList.toggle("active", active);
      if (active) activeCard = card;
    });
    state.activeUnitId = activeCard ? state.units[Number(activeCard.dataset.index)].id : null;
    const start = activeCard ? Number(activeCard.querySelector('[name="start"]').value) : null;
    const end = activeCard ? Number(activeCard.querySelector('[name="end"]').value) : null;
    document.querySelectorAll(".thumbnail-button").forEach((button) => {
      const page = Number(button.dataset.page);
      button.classList.toggle("in-active-unit", Boolean(activeCard) && start <= page && page <= end);
      button.classList.toggle("active", page === state.currentPage);
    });
    if (scroll && activeCard) {
      activeCard.scrollIntoView({ block: "nearest", inline: "nearest" });
    }
  }

  function selectPage(pageNumber, scrollUnit = false) {
    state.currentPage = Math.max(1, Math.min(state.document.numPages, pageNumber));
    synchronizeUnit(scrollUnit);
    renderPreview().catch(() => showMessage(messages.loading_error));
  }

  async function renderThumbnail(canvas, pageNumber) {
    state.activeThumbnails += 1;
    let page = null;
    let task = null;
    try {
      page = await state.document.getPage(pageNumber);
      const natural = page.getViewport({ scale: 1 });
      const viewport = page.getViewport({ scale: 150 / natural.width });
      canvas.width = Math.ceil(viewport.width);
      canvas.height = Math.ceil(viewport.height);
      task = page.render({ canvasContext: canvas.getContext("2d"), viewport });
      state.thumbnailTasks.add(task);
      await task.promise;
      canvas.previousElementSibling?.remove();
    } catch (error) {
      if (error?.name !== "RenderingCancelledException") throw error;
    } finally {
      state.thumbnailTasks.delete(task);
      state.activeThumbnails -= 1;
      page?.cleanup();
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
      button.className = "thumbnail-button mb-2 shadow-sm";
      button.dataset.page = pageNumber;
      button.setAttribute("aria-label", format(messages.view_page, { page: pageNumber }));
      button.innerHTML = '<div class="thumbnail-placeholder"></div><canvas></canvas><span></span>';
      const printedNumber = reviewData.pages[pageNumber - 1]?.printed_page_number;
      button.querySelector("span").textContent = printedNumber
        ? `${pageNumber} [${printedNumber}]` : String(pageNumber);
      button.addEventListener("click", () => selectPage(pageNumber, true));
      container.append(button);
      observer.observe(button);
    }
  }

  function option(value, selected) {
    const item = document.createElement("option");
    item.value = value;
    item.textContent = unitTypeLabels.get(value) ?? value;
    item.selected = value === selected;
    return item;
  }

  function unitField(label, name, value, type = "text") {
    const column = document.createElement("div");
    column.className = name === "title" ? "col-12" : "col-6";
    const labelElement = document.createElement("label");
    labelElement.className = "form-label d-block small mb-1";
    labelElement.textContent = label;
    const input = document.createElement("input");
    input.className = "form-control form-control-sm shadow-sm";
    input.type = type;
    input.name = name;
    input.value = value;
    if (type === "number") input.min = "1";
    labelElement.append(input);
    column.append(labelElement);
    return column;
  }

  function inclusionTargets() {
    const cards = [...document.querySelectorAll(".unit-card")];
    const scope = document.querySelector("#unit-inclusion-scope").value;
    return cards.filter((card) => {
      if (scope === "selected") return card.querySelector(".merge-unit").checked;
      const included = state.units[Number(card.dataset.index)].selected;
      if (scope === "included") return included;
      if (scope === "removed") return !included;
      return true;
    });
  }

  function updateThumbnailInclusion() {
    const ranges = [...document.querySelectorAll(".unit-card")].map((card) => ({
      start: Number(card.querySelector('[name="start"]').value),
      end: Number(card.querySelector('[name="end"]').value),
      included: state.units[Number(card.dataset.index)].selected,
    }));
    document.querySelectorAll(".thumbnail-button").forEach((button) => {
      const page = Number(button.dataset.page);
      const units = ranges.filter((range) => range.start <= page && page <= range.end);
      button.classList.toggle("excluded", units.length > 0 && units.every((unit) => !unit.included));
    });
  }

  function updateInclusionSwitch() {
    updateThumbnailInclusion();
    const targets = inclusionTargets();
    document.querySelectorAll(".unit-card").forEach((card) => {
      card.hidden = !targets.includes(card);
    });
    const included = targets.filter((card) => state.units[Number(card.dataset.index)].selected).length;
    const control = document.querySelector("#include-units");
    control.disabled = targets.length === 0;
    control.checked = targets.length > 0 && included === targets.length;
    control.indeterminate = included > 0 && included < targets.length;
    document.querySelector("#include-units-label").textContent = control.indeterminate
      ? messages.mixed : control.checked ? messages.included : messages.removed;
  }

  document.querySelector("#unit-inclusion-scope").addEventListener("change", (event) => {
    if (event.currentTarget.value === "clear-selection") {
      document.querySelectorAll(".merge-unit").forEach((checkbox) => {
        checkbox.checked = false;
      });
      event.currentTarget.value = "all";
    }
    updateInclusionSwitch();
  });
  document.querySelector("#include-units").addEventListener("change", (event) => {
    const targets = inclusionTargets();
    const included = targets.filter((card) => state.units[Number(card.dataset.index)].selected).length;
    const mixed = included > 0 && included < targets.length;
    const selected = mixed || event.currentTarget.checked;
    targets.forEach((card) => {
      const control = card.querySelector('input[id^="include-unit-"]');
      control.checked = selected;
      control.dispatchEvent(new Event("change"));
    });
    updateInclusionSwitch();
  });

  function renderUnits() {
    const container = document.querySelector("#units");
    container.replaceChildren();
    state.units.forEach((unit, index) => {
      const card = document.createElement("article");
      card.className = `unit-card border rounded p-3 mb-3${unit.newlyAdded ? " shadow" : ""}${unit.selected ? "" : " excluded"}`;
      card.dataset.index = index;

      const header = document.createElement("div");
      header.className = "d-flex align-items-center gap-2 mb-2";
      const merge = document.createElement("input");
      merge.type = "checkbox";
      merge.className = "form-check-input merge-unit shadow-sm";
      merge.setAttribute("aria-label", messages.select_merge);
      merge.addEventListener("change", updateInclusionSwitch);
      const heading = document.createElement("strong");
      heading.textContent = unit.id;
      const inclusion = document.createElement("div");
      inclusion.className = "form-check form-switch ms-auto mb-0";
      const included = document.createElement("input");
      included.type = "checkbox";
      included.className = "form-check-input shadow-sm";
      included.id = `include-unit-${index}`;
      included.setAttribute("role", "switch");
      included.setAttribute("aria-label", messages.toggle_unit);
      included.checked = unit.selected;
      const includedLabel = document.createElement("label");
      includedLabel.className = "form-check-label small";
      includedLabel.htmlFor = included.id;
      includedLabel.textContent = unit.selected ? messages.included : messages.removed;
      included.addEventListener("change", () => {
        unit.selected = included.checked;
        card.classList.toggle("excluded", !unit.selected);
        includedLabel.textContent = unit.selected ? messages.included : messages.removed;
        updateInclusionSwitch();
      });
      inclusion.append(included, includedLabel);
      header.append(merge, heading, inclusion);

      const fields = document.createElement("div");
      fields.className = "row g-2";
      fields.append(unitField(messages.title, "title", unit.title));
      const typeColumn = document.createElement("div");
      typeColumn.className = "col-12";
      const typeLabel = document.createElement("label");
      typeLabel.className = "form-label d-block small mb-1";
      typeLabel.textContent = messages.type;
      const typeSelect = document.createElement("select");
      typeSelect.className = "form-select form-select-sm shadow-sm";
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
      fields.addEventListener("input", validateUnitRanges);
      fields.addEventListener("change", () => {
        synchronizeUnit();
        validateUnitRanges();
      });
      card.addEventListener("click", () => {
        state.activeUnitId = unit.id;
        const startPage = Number(card.querySelector('[name="start"]').value);
        if (!state.document || !Number.isInteger(startPage) || startPage < 1) return;
        if (startPage !== state.currentPage) selectPage(startPage);
        else synchronizeUnit();
        const thumbnails = document.querySelector("#thumbnails");
        const thumbnail = thumbnails.querySelector(`[data-page="${state.currentPage}"]`);
        if (!thumbnail) return;
        const panelBounds = thumbnails.getBoundingClientRect();
        const thumbnailBounds = thumbnail.getBoundingClientRect();
        thumbnails.scrollTo({
          top: thumbnails.scrollTop + thumbnailBounds.top - panelBounds.top
            - thumbnails.clientTop - (thumbnails.clientHeight - thumbnailBounds.height) / 2,
          left: thumbnails.scrollLeft + thumbnailBounds.left - panelBounds.left
            - thumbnails.clientLeft - (thumbnails.clientWidth - thumbnailBounds.width) / 2,
        });
      });
      container.append(card);
    });
    synchronizeUnit();
    validateUnitRanges();
    updateInclusionSwitch();
  }

  function validateUnitRanges() {
    updateThumbnailInclusion();
    const cards = [...document.querySelectorAll(".unit-card")];
    const ranges = cards.map((card) => ({
      start: Number(card.querySelector('[name="start"]').value),
      end: Number(card.querySelector('[name="end"]').value),
    }));
    let valid = true;
    cards.forEach((card, index) => {
      const { start, end } = ranges[index];
      const invalid = !Number.isInteger(start) || !Number.isInteger(end)
        || start < 1 || start > end || end > reviewData.source.page_count;
      card.classList.toggle("invalid", invalid);
      card.querySelectorAll('[name="start"], [name="end"]').forEach((input) => {
        input.classList.toggle("is-invalid", invalid);
        input.setAttribute("aria-invalid", String(invalid));
      });
      if (invalid) valid = false;
    });
    document.querySelector("#save-plan").disabled = state.saving || !valid;
    return valid;
  }

  function readUnitCards() {
    document.querySelectorAll(".unit-card").forEach((card) => {
      const unit = state.units[Number(card.dataset.index)];
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
    const activeIndex = state.units.findIndex((unit) => unit.id === state.activeUnitId);
    const insertionIndex = activeIndex < 0 ? state.units.length : activeIndex + 1;
    const start = Math.min(reviewData.source.page_count,
      activeIndex < 0 ? state.currentPage : state.units[activeIndex].input_pages.end + 1);
    const unit = {
      id: newUnitId(),
      title: messages.add_title,
      newlyAdded: true,
      type: "unknown",
      input_pages: { start, end: start },
      selected: true,
      boundary_status: "confirmed",
    };
    state.units.splice(insertionIndex, 0, unit);
    state.activeUnitId = unit.id;
    state.currentPage = start;
    renderUnits();
    synchronizeUnit(true);
    renderPreview().catch(() => showMessage(messages.loading_error));
  }

  function mergeUnits() {
    readUnitCards();
    const selected = [...document.querySelectorAll(".merge-unit:checked")]
      .map((control) => Number(control.closest(".unit-card").dataset.index))
      .sort((first, second) => first - second);
    if (!selected.every((index) => Number.isInteger(index)
      && index >= 0 && index < state.units.length)) {
      showMessage(messages.invalid_merge);
      return;
    }
    const units = selected.map((index) => state.units[index]);
    const ordered = [...units].sort(
      (first, second) => first.input_pages.start - second.input_pages.start,
    );
    const adjacent = ordered.every(
      (unit, index) => index === 0 || Math.max(
        ...ordered.slice(0, index).map((previous) => previous.input_pages.end),
      ) + 1 >= unit.input_pages.start,
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
        end: Math.max(...ordered.map((unit) => unit.input_pages.end)),
      },
      selected: ordered.some((unit) => unit.selected),
      boundary_status: "confirmed",
    };
    state.units = state.units.filter((_unit, index) => !selected.includes(index));
    state.units.push(merged);
    state.units.sort((first, second) => first.input_pages.start - second.input_pages.start);
    renderUnits();
  }

  function compareUnitPages(first, second) {
    return first.input_pages.start - second.input_pages.start
      || first.input_pages.end - second.input_pages.end;
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
    })).sort(compareUnitPages);
    const pages = reviewData.pages.map((page) => ({
      ...page,
      selected: page.selected ?? true,
      unit_ids: units
        .filter(
          (unit) =>
            unit.input_pages.start <= page.input_page &&
            page.input_page <= unit.input_pages.end,
        )
        .map((unit) => unit.id),
    }));
    return {
      schema_version: reviewData.schema_version,
      kind: "plan",
      source: reviewData.source,
      document_type: reviewData.document_type,
      pages,
      units,
    };
  }

  async function savePlan() {
    if (state.saving || !validateUnitRanges()) return;
    state.saving = true;
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
      readUnitCards();
      state.units.sort(compareUnitPages);
      renderUnits();
      button.classList.replace("btn-success", "btn-outline-success");
      document.querySelector("#delete-plan").disabled = false;
      const target = document.querySelector("#plan-message");
      target.replaceChildren();
      const alert = document.createElement("div");
      alert.className = "alert alert-success";
      alert.textContent = payload.message;
      const link = document.createElement("a");
      link.className = "btn btn-sm btn-success ms-3 shadow-sm";
      link.href = payload.download_url;
      link.textContent = payload.download_url ? "JSON" : "";
      alert.append(link);
      target.append(alert);
    } catch (error) {
      showMessage(error.message || messages.save_error);
    } finally {
      state.saving = false;
      validateUnitRanges();
      button.textContent = oldText;
    }
  }

  document.querySelector("#delete-plan").addEventListener("htmx:afterRequest", (event) => {
    if (event.detail.successful) {
      window.location.reload();
    } else {
      showMessage(messages.delete_plan_error);
    }
  });
  document.addEventListener("click", (event) => {
    const menu = document.querySelector("#review-more");
    if (!menu.contains(event.target)) menu.open = false;
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") document.querySelector("#review-more").open = false;
  });

  document.querySelector("#previous-page").addEventListener("click", () => selectPage(state.currentPage - 1));
  document.querySelector("#next-page").addEventListener("click", () => selectPage(state.currentPage + 1));
  previewInput.addEventListener("change", () => selectPage(Number(previewInput.value)));
  document.querySelector("#zoom-in").addEventListener("click", () => {
    state.scale = Math.min(3, state.scale + 0.25);
    renderPreview().catch(() => showMessage(messages.loading_error));
  });
  document.querySelector("#zoom-out").addEventListener("click", () => {
    state.scale = Math.max(0.5, state.scale - 0.25);
    renderPreview().catch(() => showMessage(messages.loading_error));
  });
  document.querySelector("#add-unit").addEventListener("click", addUnit);
  document.querySelector("#merge-units").addEventListener("click", mergeUnits);
  document.querySelector("#save-plan").addEventListener("click", savePlan);

  const reviewToolbar = document.querySelector("#review-toolbar");
  const toolbarResizeObserver = new ResizeObserver(() => {
    app.style.setProperty(
      "--review-toolbar-height",
      `${reviewToolbar.getBoundingClientRect().height}px`,
    );
  });
  toolbarResizeObserver.observe(reviewToolbar);

  let resizeFrame = null;
  let previewPanelWidth = null;
  const previewResizeObserver = new ResizeObserver(([entry]) => {
    const width = entry.contentRect.width;
    if (width === previewPanelWidth) return;
    previewPanelWidth = width;
    cancelAnimationFrame(resizeFrame);
    resizeFrame = requestAnimationFrame(() => {
      renderPreview().catch(() => showMessage(messages.loading_error));
    });
  });
  previewResizeObserver.observe(previewPanel);

  window.addEventListener("pagehide", () => {
    previewResizeObserver.disconnect();
    toolbarResizeObserver.disconnect();
    cancelAnimationFrame(resizeFrame);
    state.previewGeneration += 1;
    state.previewTask?.cancel();
    state.textLayer?.cancel();
    state.thumbnailTasks.forEach((task) => task.cancel());
    state.thumbnailQueue.length = 0;
    state.document?.destroy();
    state.document = null;
  });

  renderUnits();
  pdfjsLib.getDocument({
    url: app.dataset.documentUrl,
    wasmUrl: new URL("./vendor/pdfjs/wasm/", import.meta.url).href,
  }).promise
    .then((document) => {
      state.document = document;
      previewInput.max = document.numPages;
      pageCount.textContent = `/ ${document.numPages}`;
      buildThumbnails();
      synchronizeUnit();
      return renderPreview();
    })
    .catch(() => showMessage(messages.loading_error));
}
