(function () {
  const WIDTH = 1024;
  const HEIGHT = 768;
  const TIMEOUT_MS = 60000;
  const BACKGROUND = 0xf7f9ff;

  const extname = (name) => {
    const index = (name || "").lastIndexOf(".");
    return index === -1 ? "" : name.slice(index).toLowerCase();
  };

  const setStatus = (widget, text, state) => {
    const status = widget.querySelector("[data-render-status]");
    const badge = widget.querySelector("[data-render-badge]");
    if (status) {
      status.textContent = text;
    }
    if (badge) {
      badge.dataset.state = state || "";
    }
    widget.dataset.renderState = state || "";
  };

  const hasWebGL = () => {
    const canvas = document.createElement("canvas");
    return Boolean(canvas.getContext("webgl") || canvas.getContext("experimental-webgl"));
  };

  const sourceViaWorker = (sourceUrl, mode) =>
    new Promise((resolve, reject) => {
      if (!window.Worker) {
        reject(new Error("Браузер не поддерживает Web Worker."));
        return;
      }

      const worker = new Worker("/static/js/model-render-worker.js");
      const id = `${Date.now()}-${Math.random()}`;
      const timeout = window.setTimeout(() => {
        worker.terminate();
        reject(new Error("Рендер не уложился в 60 секунд."));
      }, TIMEOUT_MS);

      worker.onmessage = (event) => {
        if (!event.data || event.data.id !== id) {
          return;
        }
        window.clearTimeout(timeout);
        worker.terminate();
        if (!event.data.ok) {
          reject(new Error(event.data.error || "Не удалось прочитать файл."));
          return;
        }
        resolve(event.data);
      };

      worker.onerror = () => {
        window.clearTimeout(timeout);
        worker.terminate();
        reject(new Error("Не удалось запустить обработчик файла."));
      };

      worker.postMessage({ id, sourceUrl, mode });
    });

  const objectBounds = (object) => {
    const box = new THREE.Box3().setFromObject(object);
    const size = box.getSize(new THREE.Vector3());
    const center = box.getCenter(new THREE.Vector3());
    return { center, size, maxDim: Math.max(size.x, size.y, size.z) || 1 };
  };

  const viewCamera = (bounds, viewWidth, viewHeight, position, up) => {
    const aspect = viewWidth / viewHeight;
    const viewSize = bounds.maxDim * 1.16;
    const camera = new THREE.OrthographicCamera(
      (-viewSize * aspect) / 2,
      (viewSize * aspect) / 2,
      viewSize / 2,
      -viewSize / 2,
      Math.max(bounds.maxDim / 1000, 0.01),
      bounds.maxDim * 100
    );
    const distance = bounds.maxDim * 10;
    camera.position.copy(bounds.center).addScaledVector(position.clone().normalize(), distance);
    camera.up.copy(up);
    camera.lookAt(bounds.center);
    camera.updateProjectionMatrix();
    return camera;
  };

  const drawViewLabel = (context, label, x, y) => {
    context.save();
    context.font = "600 24px Arial, sans-serif";
    context.textBaseline = "top";
    const width = context.measureText(label).width + 28;
    context.fillStyle = "rgba(247, 249, 255, 0.88)";
    context.fillRect(x + 16, y + 16, width, 38);
    context.fillStyle = "#00007f";
    context.fillText(label, x + 30, y + 23);
    context.restore();
  };

  const renderGeometries = (geometries) => {
    if (!window.THREE) {
      throw new Error("Three.js не загружен.");
    }
    if (!hasWebGL()) {
      throw new Error("В браузере недоступен WebGL.");
    }

    const renderCanvas = document.createElement("canvas");
    renderCanvas.width = WIDTH;
    renderCanvas.height = HEIGHT;
    const renderer = new THREE.WebGLRenderer({
      canvas: renderCanvas,
      antialias: true,
      preserveDrawingBuffer: true,
      alpha: false,
    });
    renderer.setSize(WIDTH, HEIGHT, false);
    renderer.setPixelRatio(1);
    renderer.setClearColor(BACKGROUND, 1);

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(BACKGROUND);
    const group = new THREE.Group();
    const material = new THREE.MeshStandardMaterial({
      color: 0x1f347f,
      roughness: 0.62,
      metalness: 0.02,
      side: THREE.DoubleSide,
    });

    geometries.forEach((geometry) => {
      geometry.computeVertexNormals();
      geometry.computeBoundingBox();
      const mesh = new THREE.Mesh(geometry, material);
      group.add(mesh);
    });

    scene.add(group);
    scene.add(new THREE.HemisphereLight(0xffffff, 0xb8c0d8, 1.25));
    const light = new THREE.DirectionalLight(0xffffff, 1.35);
    light.position.set(3, 4, 5);
    scene.add(light);

    const cellWidth = WIDTH / 2;
    const cellHeight = HEIGHT / 2;
    const bounds = objectBounds(group);
    const views = [
      {
        label: "Сверху",
        viewport: [0, cellHeight, cellWidth, cellHeight],
        labelPosition: [0, 0],
        position: new THREE.Vector3(0, 1, 0),
        up: new THREE.Vector3(0, 0, -1),
      },
      {
        label: "Сбоку",
        viewport: [cellWidth, cellHeight, cellWidth, cellHeight],
        labelPosition: [cellWidth, 0],
        position: new THREE.Vector3(1, 0, 0),
        up: new THREE.Vector3(0, 1, 0),
      },
      {
        label: "Диметрия",
        viewport: [0, 0, cellWidth, cellHeight],
        labelPosition: [0, cellHeight],
        position: new THREE.Vector3(1, 0.5, 1),
        up: new THREE.Vector3(0, 1, 0),
      },
      {
        label: "Изометрия",
        viewport: [cellWidth, 0, cellWidth, cellHeight],
        labelPosition: [cellWidth, cellHeight],
        position: new THREE.Vector3(1, 1, 1),
        up: new THREE.Vector3(0, 1, 0),
      },
    ];

    renderer.setScissorTest(true);
    views.forEach((view) => {
      const [x, y, width, height] = view.viewport;
      renderer.setViewport(x, y, width, height);
      renderer.setScissor(x, y, width, height);
      renderer.clear(true, true, true);
      renderer.render(scene, viewCamera(bounds, width, height, view.position, view.up));
    });
    renderer.setScissorTest(false);

    const outputCanvas = document.createElement("canvas");
    outputCanvas.width = WIDTH;
    outputCanvas.height = HEIGHT;
    const context = outputCanvas.getContext("2d");
    context.drawImage(renderCanvas, 0, 0);
    context.strokeStyle = "#c7d2f6";
    context.lineWidth = 2;
    context.beginPath();
    context.moveTo(cellWidth, 0);
    context.lineTo(cellWidth, HEIGHT);
    context.moveTo(0, cellHeight);
    context.lineTo(WIDTH, cellHeight);
    context.stroke();
    views.forEach((view) => drawViewLabel(context, view.label, view.labelPosition[0], view.labelPosition[1]));
    return {
      canvas: outputCanvas,
      dimensions: {
        model_width: bounds.size.x,
        model_depth: bounds.size.y,
        model_height: bounds.size.z,
      },
    };
  };

  const stlGeometry = (buffer) => {
    if (!window.THREE || !THREE.STLLoader) {
      throw new Error("STLLoader не загружен.");
    }
    return new THREE.STLLoader().parse(buffer);
  };

  const loadOcct = async () => {
    if (!window.occtimportjs) {
      throw new Error("OCCT Import JS не загружен.");
    }
    return window.occtimportjs({
      locateFile: (file) => `/static/vendor/occt/${file}`,
    });
  };

  const stepGeometries = async (buffer) => {
    const occt = await loadOcct();
    const result = occt.ReadStepFile(new Uint8Array(buffer), null);
    if (!result || !Array.isArray(result.meshes) || !result.meshes.length) {
      throw new Error("В STP-файле не найдена геометрия для рендера.");
    }

    return result.meshes.map((mesh) => {
      const position =
        mesh?.attributes?.position?.array ||
        mesh?.attributes?.position ||
        mesh?.vertices ||
        [];
      const index = mesh?.index?.array || mesh?.indices || [];
      if (!position.length) {
        throw new Error("OCCT вернул пустую геометрию.");
      }
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute("position", new THREE.BufferAttribute(new Float32Array(position), 3));
      if (index.length) {
        geometry.setIndex(Array.from(index));
      }
      return geometry;
    });
  };

  const pairList = (text) => {
    const lines = text.replace(/\r/g, "").split("\n").map((line) => line.trim());
    const pairs = [];
    for (let index = 0; index < lines.length - 1; index += 2) {
      pairs.push({ code: lines[index], value: lines[index + 1] });
    }
    return pairs;
  };

  const normalizeArcSweep = (startAngle, endAngle) => {
    let sweep = endAngle - startAngle;
    while (sweep <= 0) {
      sweep += 360;
    }
    return sweep;
  };

  const arcPoints = (entity, minSegments = 16) => {
    const sweep = normalizeArcSweep(entity.startAngle, entity.endAngle);
    const segments = Math.max(minSegments, Math.ceil(sweep / 8));
    const points = [];
    for (let index = 0; index <= segments; index += 1) {
      const angle = ((entity.startAngle + (sweep * index) / segments) * Math.PI) / 180;
      points.push({
        x: entity.x + Math.cos(angle) * entity.r,
        y: entity.y + Math.sin(angle) * entity.r,
      });
    }
    return points;
  };

  const parseDxfEntities = (text) => {
    const pairs = pairList(text);
    const entities = [];
    for (let i = 0; i < pairs.length; i += 1) {
      if (pairs[i].code !== "0") {
        continue;
      }
      const type = pairs[i].value;
      if (type === "LINE") {
        const line = { type, x1: 0, y1: 0, x2: 0, y2: 0 };
        while (++i < pairs.length && pairs[i].code !== "0") {
          const value = Number.parseFloat(pairs[i].value);
          if (pairs[i].code === "10") line.x1 = value;
          if (pairs[i].code === "20") line.y1 = value;
          if (pairs[i].code === "11") line.x2 = value;
          if (pairs[i].code === "21") line.y2 = value;
        }
        i -= 1;
        entities.push(line);
      } else if (type === "CIRCLE") {
        const circle = { type, x: 0, y: 0, r: 0 };
        while (++i < pairs.length && pairs[i].code !== "0") {
          const value = Number.parseFloat(pairs[i].value);
          if (pairs[i].code === "10") circle.x = value;
          if (pairs[i].code === "20") circle.y = value;
          if (pairs[i].code === "40") circle.r = value;
        }
        i -= 1;
        if (circle.r > 0) entities.push(circle);
      } else if (type === "ARC") {
        const arc = { type, x: 0, y: 0, r: 0, startAngle: 0, endAngle: 0 };
        while (++i < pairs.length && pairs[i].code !== "0") {
          const value = Number.parseFloat(pairs[i].value);
          if (pairs[i].code === "10") arc.x = value;
          if (pairs[i].code === "20") arc.y = value;
          if (pairs[i].code === "40") arc.r = value;
          if (pairs[i].code === "50") arc.startAngle = value;
          if (pairs[i].code === "51") arc.endAngle = value;
        }
        i -= 1;
        if (arc.r > 0) entities.push(arc);
      } else if (type === "LWPOLYLINE" || type === "POLYLINE") {
        const polyline = { type: "POLYLINE", points: [], closed: false };
        let current = null;
        while (++i < pairs.length && pairs[i].code !== "0") {
          const value = Number.parseFloat(pairs[i].value);
          if (pairs[i].code === "70") polyline.closed = (Number.parseInt(pairs[i].value, 10) & 1) === 1;
          if (pairs[i].code === "10") {
            current = { x: value, y: 0 };
            polyline.points.push(current);
          }
          if (pairs[i].code === "20" && current) current.y = value;
        }
        i -= 1;
        if (polyline.points.length > 1) entities.push(polyline);
      }
    }
    return entities;
  };

  const drawDxf = (text) => {
    const entities = parseDxfEntities(text);
    if (!entities.length) {
      throw new Error("В DXF-файле не найдены поддерживаемые примитивы.");
    }

    const points = [];
    entities.forEach((entity) => {
      if (entity.type === "LINE") {
        points.push([entity.x1, entity.y1], [entity.x2, entity.y2]);
      } else if (entity.type === "CIRCLE") {
        points.push([entity.x - entity.r, entity.y - entity.r], [entity.x + entity.r, entity.y + entity.r]);
      } else if (entity.type === "ARC") {
        arcPoints(entity).forEach((point) => points.push([point.x, point.y]));
      } else if (entity.type === "POLYLINE") {
        entity.points.forEach((point) => points.push([point.x, point.y]));
      }
    });

    const minX = Math.min(...points.map((point) => point[0]));
    const maxX = Math.max(...points.map((point) => point[0]));
    const minY = Math.min(...points.map((point) => point[1]));
    const maxY = Math.max(...points.map((point) => point[1]));
    const spanX = maxX - minX || 1;
    const spanY = maxY - minY || 1;
    const padding = 64;
    const scale = Math.min((WIDTH - padding * 2) / spanX, (HEIGHT - padding * 2) / spanY);
    const canvas = document.createElement("canvas");
    canvas.width = WIDTH;
    canvas.height = HEIGHT;
    const context = canvas.getContext("2d");
    context.fillStyle = "#f7f9ff";
    context.fillRect(0, 0, WIDTH, HEIGHT);
    context.strokeStyle = "#00007f";
    context.lineWidth = 3;
    context.lineCap = "round";
    context.lineJoin = "round";

    const x = (value) => padding + (value - minX) * scale + (WIDTH - padding * 2 - spanX * scale) / 2;
    const y = (value) => HEIGHT - padding - (value - minY) * scale - (HEIGHT - padding * 2 - spanY * scale) / 2;

    entities.forEach((entity) => {
      context.beginPath();
      if (entity.type === "LINE") {
        context.moveTo(x(entity.x1), y(entity.y1));
        context.lineTo(x(entity.x2), y(entity.y2));
      } else if (entity.type === "CIRCLE") {
        context.arc(x(entity.x), y(entity.y), entity.r * scale, 0, Math.PI * 2);
      } else if (entity.type === "ARC") {
        arcPoints(entity).forEach((point, index) => {
          if (index === 0) {
            context.moveTo(x(point.x), y(point.y));
          } else {
            context.lineTo(x(point.x), y(point.y));
          }
        });
      } else if (entity.type === "POLYLINE") {
        entity.points.forEach((point, index) => {
          if (index === 0) {
            context.moveTo(x(point.x), y(point.y));
          } else {
            context.lineTo(x(point.x), y(point.y));
          }
        });
        if (entity.closed) {
          context.closePath();
        }
      }
      context.stroke();
    });
    return {
      canvas,
      dimensions: {
        model_width: spanX,
        model_depth: spanY,
        model_height: 0,
      },
    };
  };

  const canvasToBlob = (canvas) =>
    new Promise((resolve, reject) => {
      canvas.toBlob((blob) => {
        if (blob) {
          resolve(blob);
        } else {
          reject(new Error("Не удалось создать PNG."));
        }
      }, "image/png");
    });

  const normalizedDimension = (value) => {
    const number = Number(value);
    if (!Number.isFinite(number) || number < 0) {
      return "";
    }
    return String(Math.round(number * 10) / 10);
  };

  const savePreview = async (widget, blob, dimensions) => {
    const formData = new FormData();
    formData.append("photo", new File([blob], "model-preview.png", { type: "image/png" }));
    formData.append("source_name", widget.dataset.fileName || "");
    if (dimensions) {
      ["model_width", "model_depth", "model_height"].forEach((field) => {
        formData.append(field, normalizedDimension(dimensions[field]));
      });
    }
    const response = await fetch(widget.dataset.saveUrl, {
      method: "POST",
      body: formData,
      credentials: "same-origin",
      headers: {
        "X-CSRFToken": widget.dataset.csrfToken || "",
      },
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || !payload.ok) {
      throw new Error(payload.error || "Не удалось сохранить изображение.");
    }
    return payload;
  };

  const showBlobPreview = (widget, blob) => {
    const preview = widget.querySelector("[data-photo-preview]");
    if (!preview) {
      return;
    }
    const oldUrl = preview.dataset.previewUrl;
    if (oldUrl) {
      URL.revokeObjectURL(oldUrl);
    }
    const url = URL.createObjectURL(blob);
    preview.dataset.previewUrl = url;
    preview.src = url;
    preview.classList.remove("hidden");
  };

  const renderWidget = async (widget) => {
    if (widget.dataset.hasPreview === "1") {
      return;
    }
    const sourceUrl = widget.dataset.sourceUrl;
    const fileName = widget.dataset.fileName || "";
    const extension = extname(fileName);
    if (!sourceUrl || !extension) {
      setStatus(widget, "Не найден производственный файл. Вернитесь на предыдущий шаг.", "error");
      return;
    }

    try {
      setStatus(widget, "Читаем производственный файл...", "loading");
      let result;
      if (extension === ".dxf") {
        const { text } = await sourceViaWorker(sourceUrl, "dxf");
        setStatus(widget, "Строим изображение DXF...", "loading");
        result = drawDxf(text);
      } else {
        const { buffer } = await sourceViaWorker(sourceUrl, "binary");
        setStatus(widget, "Строим изображение 3D-модели...", "loading");
        if (extension === ".stl") {
          result = renderGeometries([stlGeometry(buffer)]);
        } else if (extension === ".stp") {
          result = renderGeometries(await stepGeometries(buffer));
        } else {
          throw new Error("Формат файла не поддерживается для автопревью.");
        }
      }

      const blob = await canvasToBlob(result.canvas);
      showBlobPreview(widget, blob);
      await savePreview(widget, blob, result.dimensions);
      widget.dataset.hasPreview = "1";
      setStatus(widget, "Изображение построено и сохранено.", "ready");
    } catch (error) {
      setStatus(
        widget,
        `${error.message || "Не удалось построить изображение."} Загрузите изображение вручную.`,
        "error"
      );
    }
  };

  document.addEventListener("DOMContentLoaded", () => {
    const widgets = Array.from(document.querySelectorAll("[data-model-preview]"));
    widgets.reduce((chain, widget) => chain.then(() => renderWidget(widget)), Promise.resolve());
  });
})();
