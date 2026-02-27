# Scalabel Annotation Tool — Setup & Run Guide (Ubuntu / Linux)

This guide walks through everything needed to set up and run the Scalabel
annotation server from scratch on Ubuntu (20.04 / 22.04 LTS).

---

## Table of Contents

1. [System Dependencies](#1-system-dependencies)
2. [Install Node.js](#2-install-nodejs-v18)
3. [Install Redis](#3-install-redis)
4. [Install Miniconda (Python)](#4-install-miniconda-python)
5. [Create the Python Environment](#5-create-the-python-environment)
6. [Clone / Enter the Project](#6-clone--enter-the-project)
7. [Configure Local Storage](#7-configure-local-storage)
8. [Install Node Packages](#8-install-node-packages)
9. [Build the Frontend](#9-build-the-frontend)
10. [Start the Server](#10-start-the-server)
11. [Development Mode (auto-recompile)](#11-development-mode-auto-recompile)
12. [Loading Images for Annotation](#12-loading-images-for-annotation)
13. [Stopping the Server](#13-stopping-the-server)
14. [Troubleshooting](#14-troubleshooting)

---

## 1. System Dependencies

```bash
sudo apt update && sudo apt upgrade -y

# Build tools needed by some Python packages (pycocotools, Cython, etc.)
sudo apt install -y \
    build-essential \
    git \
    curl \
    wget \
    unzip \
    libssl-dev \
    libffi-dev \
    python3-dev \
    libgl1-mesa-glx \
    libglib2.0-0
```

---

## 2. Install Node.js v18

Use the official NodeSource setup so you get a modern, stable version.

```bash
curl -fsSL https://deb.nodesource.com/setup_18.x | sudo -E bash -
sudo apt install -y nodejs

# Verify
node --version   # should print v18.x.x
npm --version    # should print 9.x or 10.x
```

> **Why v18?** The project was tested with Node 18 LTS. Node 20+ can cause
> native addon build failures (e.g. `canvas`), which are worked around below.

---

## 3. Install Redis

Redis is required by the annotation server to manage sessions and queues.

```bash
sudo apt install -y redis-server

# Check it starts correctly
sudo systemctl start redis-server
sudo systemctl enable redis-server   # auto-start on boot (optional)

# Verify
redis-cli ping    # should reply: PONG
```

If you prefer NOT to run Redis as a system service, you can start it manually
before launching the annotation server each time:

```bash
redis-server --daemonize yes
```

---

## 4. Install Miniconda (Python)

Skip this step if you already have Conda or Miniconda installed.

```bash
wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh -O ~/miniconda.sh
bash ~/miniconda.sh -b -p ~/miniconda3
~/miniconda3/bin/conda init bash
source ~/.bashrc

# Verify
conda --version
```

---

## 5. Create the Python Environment

The annotation-only environment (~600 MB — no PyTorch / CUDA / detectron2).

```bash
# From the project root
conda create -n Scalabel-Annotate python=3.8.13 -y
conda activate Scalabel-Annotate

pip install --upgrade pip setuptools wheel

# Install all Python dependencies
pip install -r scripts/requirements_annotate.txt

# Install the scalabel Python package in editable mode
pip install -e .
```

> After this step `python -c "import scalabel"` should succeed without errors.

---

## 6. Clone / Enter the Project

If you haven't cloned the repo yet:

```bash
git clone https://github.com/<your-fork-or-org>/scalabel.git
cd scalabel
```

If the repo is already on disk just `cd` into it:

```bash
cd /path/to/scalabel
```

---

## 7. Configure Local Storage

Create the directories Scalabel uses to store projects, labels, and images:

```bash
mkdir -p local-data/scalabel
mkdir -p local-data/items
```

Create the config file:

```bash
cat > local-data/scalabel/config.yml << 'EOF'
http:
    port: 8686
storage:
    data: "./local-data/scalabel"
    itemDir: "./local-data/items"
    type: "local"
EOF
```

| Config key | What it controls |
|---|---|
| `port` | HTTP port the annotation UI serves on |
| `storage.data` | Where projects / label JSON files are saved |
| `storage.itemDir` | Where image files are served from |

---

## 8. Install Node Packages

```bash
# --ignore-scripts skips native addon compilation (e.g. canvas@2.9.1)
# which fails on many environments. The annotation UI does not need it.
npm install --ignore-scripts
```

This installs ~1670 packages into `node_modules/`. It only needs to be run
once (or after `package.json` changes).

---

## 9. Build the Frontend

Compiles all TypeScript/React source into optimised JavaScript bundles.

```bash
npm run build
```

Expected output (takes ~2–5 minutes on first run):

```
webpack 5.x.x compiled successfully in xxxxx ms
webpack 5.x.x compiled successfully in xxxxx ms
```

The compiled files land in `app/dist/`.

---

## 10. Start the Server

You need **two** processes running at the same time: Redis and the Node server.

### Terminal 1 — Redis (if not running as a system service)

```bash
redis-server --daemonize yes
```

### Terminal 2 — Annotation Server

```bash
node --max-old-space-size=8192 app/dist/main.js \
    --config ./local-data/scalabel/config.yml
```

Expected output:

```
info: Using scalabel data dir ./local-data/scalabel ...
info: Starting HTTP server at Port 8686
```

Open your browser and go to:

```
http://localhost:8686
```

> **`--max-old-space-size=8192`** gives Node 8 GB heap — recommended when
> annotating large image sets. Lower it (e.g. 4096) on machines with less RAM.

---

## 11. Development Mode (auto-recompile)

When actively modifying source files, use watch mode instead of `npm run build`.
Webpack will automatically recompile whenever a `.ts` / `.tsx` file is saved
(incremental rebuild takes ~8–15 s).

```bash
# Terminal 1 — webpack watch (leave running)
npm run watch-dev

# Terminal 2 — annotation server (leave running)
node --max-old-space-size=8192 app/dist/main.js \
    --config ./local-data/scalabel/config.yml
```

After webpack prints `compiled successfully`, **hard-refresh** the browser
(`Ctrl+Shift+R`) to load the updated bundles.

---

## 12. Loading Images for Annotation

### Option A — Copy images into the item directory

```bash
cp /path/to/your/images/*.jpg local-data/items/
```

### Option B — Symlink a directory

```bash
ln -s /path/to/your/dataset local-data/items/dataset
```

### Create a project

1. Go to `http://localhost:8686`
2. Click **Create Project**
3. Fill in:
   - **Project name** — any name
   - **Item type** — `image`
   - **Label type** — `segmentation` (for polygon annotation)
   - **Item list** — a `.yml` or `.json` file listing paths to your images
   - **Categories** — a `.yml` file listing your label classes

#### Minimal item list YAML (`image_list.yml`)

```yaml
- {url: "http://localhost:8686/items/image1.jpg", name: "image1"}
- {url: "http://localhost:8686/items/image2.jpg", name: "image2"}
```

#### Minimal categories YAML (`categories.yml`)

```yaml
- name: class_one
- name: class_two
- name: class_three
```

See `examples/` in the project root for more complete examples.

---

## 13. Stopping the Server

```bash
# Kill the Node server
Ctrl+C   (in the terminal running node)

# Stop Redis (if started manually)
redis-cli shutdown

# Or if running as a system service
sudo systemctl stop redis-server
```

---

## 14. Troubleshooting

### `redis-server: command not found` after manual install

```bash
sudo apt install -y redis-server
which redis-server   # should print /usr/bin/redis-server
```

### `Error: Cannot find module` when starting the server

The build has not been run yet, or ran with errors. Re-run:

```bash
npm run build
```

### `EADDRINUSE: address already in use :::8686`

Another process is using port 8686. Either stop it or change `port` in
`local-data/scalabel/config.yml` to another value (e.g. `8787`).

```bash
# Find what is using the port
sudo lsof -i :8686
```

### `canvas` npm build error

Expected — and harmless. The `--ignore-scripts` flag prevents it from blocking
the install. The annotation UI does not use the native `canvas` module.

### Python `import scalabel` fails

Make sure the conda env is activated and the editable install was done:

```bash
conda activate Scalabel-Annotate
pip install -e .
```

### Out of memory at high zoom

Reduce the heap limit if RAM is limited:

```bash
node --max-old-space-size=4096 app/dist/main.js \
    --config ./local-data/scalabel/config.yml
```

### Which browser to use?

**Chrome (recommended)** — best Canvas 2D GPU rendering performance.
The annotation UI is canvas-heavy; Chrome's Skia/GPU compositor handles
large zoomed canvases significantly better than Firefox.

---

## Quick-Start Cheatsheet

```bash
# Every time you want to annotate:

conda activate Scalabel-Annotate          # activate Python env (if needed)
redis-server --daemonize yes              # start Redis (skip if using systemd)

node --max-old-space-size=8192 \
    app/dist/main.js \
    --config ./local-data/scalabel/config.yml

# Then open: http://localhost:8686
```
