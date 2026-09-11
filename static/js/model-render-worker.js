self.onmessage = async (event) => {
  const { id, sourceUrl, mode } = event.data || {};
  try {
    const response = await fetch(sourceUrl, { credentials: "same-origin" });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const buffer = await response.arrayBuffer();
    if (mode === "dxf") {
      const text = new TextDecoder("utf-8").decode(buffer);
      self.postMessage({ id, ok: true, text });
      return;
    }
    self.postMessage({ id, ok: true, buffer }, [buffer]);
  } catch (error) {
    self.postMessage({ id, ok: false, error: error.message || "Не удалось прочитать файл." });
  }
};
