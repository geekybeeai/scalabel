# Scalabel — Windows Setup & Startup Guide

---

## Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| **Node.js** | v18 LTS | [nodejs.org](https://nodejs.org/) — use the LTS installer |
| **Git** | any | [git-scm.com](https://git-scm.com/download/win) |
| **Visual Studio Build Tools** | 2019+ | [Download](https://visualstudio.microsoft.com/visual-cpp-build-tools/) — select **"Desktop development with C++"** workload |
| **Miniconda** *(optional)* | latest | [miniconda](https://docs.conda.io/en/latest/miniconda.html) — only needed for Python API features |

> **Redis**: The default config uses `type: "local"` file storage, so Redis is **not required**. If you later switch to Redis storage, install [Memurai](https://www.memurai.com/) or run `docker run -d -p 6379:6379 redis`.

---

## One-Time Installation

Open **PowerShell** and run the following from the project root:

### 1. Clone (skip if already done)

```powershell
git clone https://github.com/scalabel/scalabel.git
cd scalabel
```

### 2. Set up local data directories

```powershell
New-Item -ItemType Directory -Force -Path "local-data\scalabel"
New-Item -ItemType Directory -Force -Path "local-data\items\examples"
Copy-Item "examples\cat.webp" "local-data\items\examples\" -Force
Copy-Item "app\config\default_config.yml" "local-data\scalabel\config.yml" -Force
```

This creates the config at `local-data/scalabel/config.yml`:

```yaml
http:
    port: 8686
storage:
    data: "./local-data/scalabel"
    itemDir: "./local-data/items"
    type: "local"
```

### 3. Install Node packages

```powershell
npm install --ignore-scripts
```

> `--ignore-scripts` skips the native `canvas` module build which fails on most Windows setups. The annotation UI does not need it.

### 4. Build the project

```powershell
npm run build
```

This compiles TypeScript/React into `app/dist/`. Takes ~2–5 min on first run.

### 5. Python environment *(optional — only for Python API)*

```powershell
# In Anaconda Prompt:
conda create -n Scalabel python=3.8.13 -y
conda activate Scalabel
pip install --upgrade pip setuptools wheel
pip install -e .
```

---

## Startup Cheat Sheet

```powershell
cd d:\Nikhil\Projects\GitHub\scalabel

# Start the server
node --max-old-space-size=8192 app/dist/main.js --config ./local-data/scalabel/config.yml

# Or simply:
npm run serve
```

**Open** → [http://localhost:8686](http://localhost:8686)

### Development mode (auto-recompile on save)

```powershell
# Terminal 1 — watch mode
npm run watch-dev

# Terminal 2 — server
npm run serve
```

After webpack prints `compiled successfully`, hard-refresh browser with `Ctrl+Shift+R`.

---

## Loading Images

### Copy images into the items directory

```powershell
Copy-Item "D:\path\to\images\*" "local-data\items\" -Recurse
```

### Or symlink a folder (run PowerShell as Admin)

```powershell
New-Item -ItemType SymbolicLink -Path "local-data\items\dataset" -Target "D:\path\to\dataset"
```

### Create a project

1. Go to `http://localhost:8686` → **Create Project**
2. Set **Item type** = `image`, **Label type** = `segmentation` (polygon/polyline)
3. Upload an item list YAML:

```yaml
- {url: "http://localhost:8686/items/image1.jpg", name: "image1"}
- {url: "http://localhost:8686/items/image2.jpg", name: "image2"}
```

4. Upload a categories YAML:

```yaml
- name: class_one
- name: class_two
```

---

## Stopping

Press `Ctrl+C` in the terminal running the Node server.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `Error: Cannot find module` | Run `npm run build` first |
| `EADDRINUSE :::8686` | Another process on port 8686. Find it: `netstat -ano \| findstr :8686`, then `Stop-Process -Id <PID>` |
| `canvas` build error during install | Use `npm install --ignore-scripts` — the native canvas module is not needed |
| Out of memory | Lower heap: `node --max-old-space-size=4096 app/dist/main.js --config ./local-data/scalabel/config.yml` |
| **Recommended browser** | Chrome — best Canvas 2D GPU rendering for annotation |
