#!/usr/bin/env node

"use strict";

const fs = require("fs");
const path = require("path");

function parseArgs(argv) {
  const args = {};
  for (let index = 0; index < argv.length; index += 2) {
    const flag = argv[index];
    const value = argv[index + 1];
    if (!flag || !flag.startsWith("--") || value === undefined) {
      throw new Error(`Invalid argument near ${flag || "end of command"}`);
    }
    args[flag.slice(2)] = value;
  }
  return args;
}

function requireArg(args, name) {
  const value = args[name];
  if (!value) {
    throw new Error(`Missing required argument --${name}`);
  }
  return value;
}

function resolveJsonInput(inputPath, label) {
  const resolved = path.resolve(inputPath);
  const stat = fs.statSync(resolved);
  if (stat.isFile()) {
    return resolved;
  }
  if (!stat.isDirectory()) {
    throw new Error(`${label} must be a JSON file or directory`);
  }
  const files = fs.readdirSync(resolved)
    .filter((name) => name.toLowerCase().endsWith(".json"));
  if (files.length !== 1) {
    throw new Error(`${label} directory must contain exactly one JSON file; found ${files.length}`);
  }
  return path.join(resolved, files[0]);
}

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, "utf8"));
}

function frameIdentity(frame) {
  return frame.name || frame.url;
}

function indexFrames(frames, label) {
  if (!Array.isArray(frames)) {
    throw new Error(`${label} export must contain a frames array`);
  }
  const index = new Map();
  for (const frame of frames) {
    const identity = frameIdentity(frame);
    if (!identity) {
      throw new Error(`${label} export contains a frame without name or url`);
    }
    if (index.has(identity)) {
      throw new Error(`${label} export contains duplicate frame identity: ${identity}`);
    }
    index.set(identity, frame);
  }
  return index;
}

function taskIdentities(taskDocuments) {
  const identities = [];
  const seen = new Set();
  for (const document of taskDocuments) {
    if (!Array.isArray(document.items)) {
      throw new Error("Task manifest must contain an items array");
    }
    for (const item of document.items) {
      const urls = Object.values(item.urls || {});
      if (urls.length !== 1 || typeof urls[0] !== "string") {
        throw new Error(`Task item ${item.id || "<unknown>"} must contain exactly one image URL`);
      }
      const identity = urls[0];
      if (seen.has(identity)) {
        throw new Error(`Requested tasks contain duplicate image identity: ${identity}`);
      }
      seen.add(identity);
      identities.push(identity);
    }
  }
  return identities;
}

function mergeTaskAnnotations(original, corrected, taskDocuments) {
  const originalFrames = indexFrames(original.frames, "Original");
  const correctedFrames = indexFrames(corrected.frames, "Corrected");
  const identities = taskIdentities(taskDocuments);

  for (const identity of identities) {
    if (!originalFrames.has(identity)) {
      throw new Error(`Task image is missing from original export: ${identity}`);
    }
    if (!correctedFrames.has(identity)) {
      throw new Error(`Task image is missing from corrected export: ${identity}`);
    }
  }

  const requested = new Set(identities);
  const frames = original.frames.map((originalFrame) => {
    const identity = frameIdentity(originalFrame);
    if (!requested.has(identity)) {
      return originalFrame;
    }
    return {
      ...originalFrame,
      labels: correctedFrames.get(identity).labels,
    };
  });

  return {
    merged: {...original, frames},
    frameCount: identities.length,
  };
}

function defaultOutputPath(originalPath, taskIndexes) {
  const extension = path.extname(originalPath);
  const base = path.basename(originalPath, extension);
  return path.join(
    path.dirname(originalPath),
    `${base}_tasks_${taskIndexes.join("_")}_merged${extension}`,
  );
}

function run(argv) {
  const args = parseArgs(argv);
  const originalPath = resolveJsonInput(requireArg(args, "original"), "Original");
  const correctedPath = resolveJsonInput(requireArg(args, "corrected"), "Corrected");
  const projectDir = path.resolve(requireArg(args, "project-dir"));
  const taskIndexes = requireArg(args, "tasks").split(",").map((value) => {
    const parsed = Number(value);
    if (!Number.isInteger(parsed) || parsed < 0) {
      throw new Error(`Invalid task index: ${value}`);
    }
    return parsed;
  });
  const outputPath = args.output
    ? path.resolve(args.output)
    : defaultOutputPath(originalPath, taskIndexes);

  if (path.resolve(outputPath) === originalPath || path.resolve(outputPath) === correctedPath) {
    throw new Error("Output path must not overwrite either input file");
  }
  if (fs.existsSync(outputPath)) {
    throw new Error(`Output file already exists: ${outputPath}`);
  }

  const taskDocuments = taskIndexes.map((taskIndex) => {
    const fileName = `${String(taskIndex).padStart(6, "0")}.json`;
    return readJson(path.join(projectDir, "tasks", fileName));
  });
  const result = mergeTaskAnnotations(
    readJson(originalPath),
    readJson(correctedPath),
    taskDocuments,
  );

  fs.mkdirSync(path.dirname(outputPath), {recursive: true});
  fs.writeFileSync(outputPath, `${JSON.stringify(result.merged, null, 2)}\n`);
  process.stdout.write(`Merged labels for ${result.frameCount} frames.\nOutput: ${outputPath}\n`);
}

if (require.main === module) {
  try {
    run(process.argv.slice(2));
  } catch (error) {
    process.stderr.write(`Error: ${error.message}\n`);
    process.exitCode = 1;
  }
}

module.exports = {
  defaultOutputPath,
  mergeTaskAnnotations,
  parseArgs,
  resolveJsonInput,
  run,
};
