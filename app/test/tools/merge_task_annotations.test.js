const fs = require("fs");
const os = require("os");
const path = require("path");
const {spawnSync} = require("child_process");

const SCRIPT_PATH = path.resolve(
  __dirname,
  "../../../tools/merge_task_annotations.js",
);

function writeJson(filePath, value) {
  fs.mkdirSync(path.dirname(filePath), {recursive: true});
  fs.writeFileSync(filePath, `${JSON.stringify(value, null, 2)}\n`);
}

function frame(name, labels, attributes = {}) {
  return {
    name,
    url: name,
    videoName: "",
    timestamp: 0,
    attributes,
    labels,
    sensor: -1,
  };
}

function task(urls) {
  return {
    items: urls.map((url, index) => ({
      id: String(index),
      urls: {"-1": url},
    })),
  };
}

describe("merge_task_annotations CLI", () => {
  let tempDir;

  beforeEach(() => {
    tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "scalabel-merge-"));
  });

  afterEach(() => {
    fs.rmSync(tempDir, {recursive: true, force: true});
  });

  test("copies labels only for images in the requested task manifests", () => {
    const originalDir = path.join(tempDir, "original");
    const correctedDir = path.join(tempDir, "corrected");
    const projectDir = path.join(tempDir, "project");
    const outputPath = path.join(tempDir, "merged.json");
    const originalPath = path.join(originalDir, "original.json");
    const correctedPath = path.join(correctedDir, "corrected.json");

    const original = {
      config: {project: "original-config"},
      frames: [
        frame("image-a.png", [{id: "original-a"}], {owner: "original-a"}),
        frame("image-b.png", [{id: "original-b"}], {owner: "original-b"}),
        frame("image-c.png", [{id: "original-c"}], {owner: "original-c"}),
      ],
    };
    const corrected = {
      config: {project: "corrected-config"},
      frames: [
        frame("image-a.png", [{id: "corrected-a"}], {owner: "corrected-a"}),
        frame("image-b.png", [{id: "corrected-b"}], {owner: "corrected-b"}),
        frame("image-c.png", [{id: "corrected-c"}], {owner: "corrected-c"}),
      ],
    };

    writeJson(originalPath, original);
    writeJson(correctedPath, corrected);
    writeJson(path.join(projectDir, "tasks", "000000.json"), task(["image-a.png"]));
    writeJson(path.join(projectDir, "tasks", "000004.json"), task(["image-c.png"]));

    const result = spawnSync(process.execPath, [
      SCRIPT_PATH,
      "--original", originalDir,
      "--corrected", correctedDir,
      "--project-dir", projectDir,
      "--tasks", "0,4",
      "--output", outputPath,
    ], {encoding: "utf8"});

    expect(result.status).toBe(0);
    const merged = JSON.parse(fs.readFileSync(outputPath, "utf8"));
    expect(merged.config).toEqual(original.config);
    expect(merged.frames).toEqual([
      frame("image-a.png", [{id: "corrected-a"}], {owner: "original-a"}),
      frame("image-b.png", [{id: "original-b"}], {owner: "original-b"}),
      frame("image-c.png", [{id: "corrected-c"}], {owner: "original-c"}),
    ]);
    expect(JSON.parse(fs.readFileSync(originalPath, "utf8"))).toEqual(original);
    expect(result.stdout).toContain("Merged labels for 2 frames");
  });
});
