document.addEventListener("DOMContentLoaded", () => {
  const applyButtons = Array.from(document.querySelectorAll("[data-bulk-material-apply]"));

  applyButtons.forEach((button) => {
    button.addEventListener("click", () => {
      const productionType = button.dataset.bulkMaterialApply;
      const source = document.querySelector(`[data-bulk-material-source="${productionType}"]`);
      const materialId = source ? source.value : "";
      if (!materialId) {
        return;
      }

      const directSelects = Array.from(
        document.querySelectorAll(`select[data-bulk-material-row="${productionType}"]`),
      );
      const cellSelects = Array.from(
        document.querySelectorAll(`[data-bulk-material-cell="${productionType}"] select`),
      );
      const rowSelects = Array.from(new Set([...directSelects, ...cellSelects]));
      rowSelects.forEach((select) => {
        const hasOption = Array.from(select.options).some((option) => option.value === materialId);
        if (hasOption) {
          select.value = materialId;
          select.dispatchEvent(new Event("change", { bubbles: true }));
        }
      });
    });
  });
});
