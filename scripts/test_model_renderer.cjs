const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

const source = fs.readFileSync(path.join(__dirname, "../static/js/model-renderer.js"), "utf8");

function renderer(overrides = {}) {
  const context = vm.createContext({
    document: {
      addEventListener() {},
      createElement() {
        return { getContext: () => new Proxy({}, { get: () => () => {} }) };
      },
    },
    ...overrides,
  });
  // Expose private functions only inside the test VM, without adding a browser API.
  vm.runInContext(source.replace(/\}\)\(\);\s*$/, "globalThis.renderer = { drawDxf, renderWidget }; })();"), context);
  return context.renderer;
}

function drawing(units, newline = "\n") {
  const header = units === undefined ? "" : `9\n$INSUNITS\n70\n${units}\n`;
  return (`0\nSECTION\n2\nHEADER\n${header}0\nENDSEC\n`
    + "0\nSECTION\n2\nENTITIES\n0\nLINE\n10\n0\n20\n0\n11\n10\n21\n4\n0\nENDSEC\n0\nEOF\n")
    .replace(/\n/g, newline);
}

test("DXF dimensions honor drawing units", () => {
  const api = renderer();
  for (const [units, scale] of [[4, 1], [5, 10], [6, 1000], [1, 25.4], [0, 1], [undefined, 1]]) {
    for (const newline of ["\n", "\r\n"]) {
      const text = drawing(units, newline);
      const result = api.drawDxf(text);
      assert.equal(result.dimensions.model_width, 10 * scale);
      assert.equal(result.dimensions.model_depth, 4 * scale);
      assert.equal(result.dimensions.model_height, 0);
      assert.equal(result.canvas.width, 1024);
      assert.equal(result.canvas.height, 768);
    }
  }
});

test("unknown DXF units do not produce misleading millimeter dimensions", () => {
  assert.deepEqual(Object.keys(renderer().drawDxf(drawing(999)).dimensions), []);
});

test("both STEP extensions route binary data to OCCT", async () => {
  for (const fileName of ["part.stp", "part.step", "PART.STEP"]) {
    let parsed = false;
    const status = { dataset: {}, textContent: "" };
    const api = renderer({
      window: {
        Worker: true,
        setTimeout,
        clearTimeout,
        occtimportjs: async () => ({
          ReadStepFile(bytes) {
            assert.equal(bytes.length, 3);
            parsed = true;
            return { meshes: [] };
          },
        }),
      },
      Worker: class {
        postMessage({ id, mode }) {
          assert.equal(mode, "binary");
          queueMicrotask(() => this.onmessage({ data: { id, ok: true, buffer: new Uint8Array([1, 2, 3]).buffer } }));
        }
        terminate() {}
      },
    });
    await api.renderWidget({
      dataset: { sourceUrl: "/source", fileName },
      querySelector: () => status,
    });
    assert.equal(parsed, true, fileName);
    assert.equal(status.dataset.state, "error");
    assert.match(status.textContent, /STP/);
  }
});
