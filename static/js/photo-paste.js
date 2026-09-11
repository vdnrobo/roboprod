document.addEventListener("DOMContentLoaded", () => {
  const zones = Array.from(document.querySelectorAll("[data-photo-paste-zone], [data-model-preview]"));
  if (!zones.length) {
    return;
  }

  let activeZone = zones[0];

  const setActiveZone = (zone) => {
    activeZone = zone;
    zones.forEach((item) => item.classList.toggle("paste-zone--active", item === zone));
  };

  const showPreview = (zone, file) => {
    const preview = zone.querySelector("[data-photo-preview]");
    if (!preview) {
      return;
    }
    const oldPreviewUrl = preview.dataset.previewUrl;
    if (oldPreviewUrl) {
      URL.revokeObjectURL(oldPreviewUrl);
      delete preview.dataset.previewUrl;
    }
    if (!file || !file.type.startsWith("image/")) {
      preview.classList.add("hidden");
      if (!preview.getAttribute("src")?.startsWith("/")) {
        preview.removeAttribute("src");
      }
      return;
    }
    const previewUrl = URL.createObjectURL(file);
    preview.dataset.previewUrl = previewUrl;
    preview.src = previewUrl;
    preview.classList.remove("hidden");
  };

  zones.forEach((zone) => {
    const input = zone.querySelector("[data-photo-input], input[type='file'][accept*='image']");
    if (!input) {
      return;
    }

    input.addEventListener("focus", () => setActiveZone(zone));
    input.addEventListener("click", () => setActiveZone(zone));
    zone.addEventListener("pointerdown", () => setActiveZone(zone));
    zone.addEventListener("focusin", () => setActiveZone(zone));
    input.addEventListener("change", () => showPreview(zone, input.files[0]));
  });

  document.addEventListener("paste", (event) => {
    const zone = activeZone || zones[0];
    const input = zone?.querySelector("[data-photo-input], input[type='file'][accept*='image']");
    if (!input) {
      return;
    }

    const items = Array.from(event.clipboardData?.items || []);
    const imageItem = items.find((item) => item.type.startsWith("image/"));
    if (!imageItem) {
      return;
    }

    const file = imageItem.getAsFile();
    if (!file) {
      return;
    }

    const extension = file.type.split("/")[1] || "png";
    const pastedFile = new File([file], `pasted-photo.${extension}`, { type: file.type });
    const transfer = new DataTransfer();
    transfer.items.add(pastedFile);
    input.files = transfer.files;
    showPreview(zone, pastedFile);
    setActiveZone(zone);
    event.preventDefault();
  });
});
