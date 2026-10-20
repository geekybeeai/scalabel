# Batch Review

A web page for reviewing Scalabel projects one batch (project) at a time, at http://localhost:8687. The projects, their labels and their images all come **live from the Scalabel server**, so no export files are needed. It shows:

- the original image and the annotated image side by side, in Scalabel's colours, with zoom and pan kept in step;
- Scalabel-style navigation: previous / next, a number box to jump, and a slider;
- "Open in Scalabel" on the same image;
- the image's lat / lon (copyable), with Google Maps and Street View links;
- a link to the image's CAD picture;
- a checkbox per class, plus line width, opacity and brightness controls.

## Setup (any system: Windows, macOS, Linux)

It needs only **Node.js 16 or newer**, which Scalabel already needs. There is nothing to install: no `npm install`, no Python.

1. Copy the `batch_review` folder anywhere.
2. Copy `config.example.json` to `config.json` and set `scalabel_url`, the CAD folder and the lat/lon CSV (see below).
3. Start Scalabel, then run `node server.js` inside the folder.
4. Open http://localhost:8687.

```bash
cd batch_review
node server.js                      # uses config.json in this folder
node server.js --config other.json  # another config
node server.js --port 8690          # another port
```

It prints:
- how many lat/lon rows and CAD images it found;
- how many Scalabel projects it lists;
- any warnings, for example a path that does not exist or Scalabel not running.

To stop it, press Ctrl+C.

## config.json

A relative path is resolved from the folder that holds the config file. On Windows, write paths with `/`, as in `C:/scalabel/local-data`, or escape backslashes as `C:\\scalabel\\local-data`.

| Key | What it is |
|---|---|
| `scalabel_url` | The Scalabel server, e.g. `http://localhost:8686`. The project list, labels and images are read from it, and "Open in Scalabel" links to it. |
| `projects` | Optional list of project names to show, in that order. When empty, every Scalabel project is listed except those matching `hide_projects`. |
| `hide_projects` | Regular expression for projects to leave out of the list. The default `_fix_\|^zz_` hides the QA review rounds and test projects. |
| `live_cache_seconds` | How long a project's labels are reused before Scalabel is asked again (default 30). **Refresh** always fetches them fresh. |
| `task_size` | Scalabel project task size (50 for these projects). Used to work out the image's task and item. |
| `cad_dir` | Folder of CAD images, searched at any depth. |
| `geo_csv` | A CSV file, or a list of them, holding image name, lat and lon (see below). The default is `image_locations.csv` in this folder. |
| `image_root` | Optional. A local folder the frame urls start from, e.g. `.../local-data`. When it's set and the file is there, the image is read from disk; otherwise it loads from Scalabel. |
| `port`, `host` | Default `8687` / `127.0.0.1`. Set `host` to `0.0.0.0` to open the page from other machines on the network. |

## Matching images to CAD images and CSV rows

Images are matched by name, ignoring the folder, the extension, a trailing `_verification` / `_input` / `_z20`, upper/lower case, and the difference between `-`, `_` and spaces. So `854_verification.png`, `854.png` and `854_input.png` all match each other.

**CSV.** The file needs a header row. Its separator can be `,`, `;` or tab. The tool picks the columns as follows:
- **image:** a header named `image`, `image_name`, `name`, `file` or `filename`, or else one containing "image", "name" or "file";
- **lat:** a header named `lat` or `latitude`, or else one containing "lat";
- **lon:** a header named `lon`, `lng`, `long` or `longitude`, or else one containing "lon" or "lng".

Rows with a bad lat/lon are skipped and reported as warnings.

```csv
image_name,latitude,longitude
854_verification.png,33.7701234,-118.1937456
```

## Using it

- **Labels are live:** they come from Scalabel (`/getExport`) and are reused for up to `live_cache_seconds`.
- **Refresh:** fetches the latest labels and project list from Scalabel, and re-reads the lat/lon CSV and the CAD folder. There is no need to restart.
- **Scalabel not running:** the top bar says so. Start Scalabel, then press Refresh.
- **Find image by name:** jumps to an image in the batch.
- The page remembers your place: the last batch, the image in each batch, the hidden classes and the display settings. The address bar has `#b=<batch>&i=<number>`, so a copied address opens the same image.
- **Keys:**

  | Key | Action |
  |---|---|
  | ← / → | previous / next image |
  | G | jump to an image number |
  | F | fit to window |
  | + / − | zoom in / out |
  | O | open in Scalabel |
  | M | Google Maps |
  | S | Street View |
  | L | copy "lat, lon" |
  | H | hide / show all lines |
  | 1 … 9 | toggle a class |
  | B | hide / show the side panel |
  | V | next layout |
  | ? | help |

- **Mouse:**
  - Scroll to zoom, as in Scalabel.
  - A trackpad side-swipe, tilt wheel or **Shift + scroll** scrolls the image left / right.
  - Drag to pan; double-click to zoom in.
  - Either image can be used; both move together.
- **More room for the images:**
  - ☰ (or **B**) hides the side panel.
  - **Layout** (or **V**) switches between side by side and top and bottom. "Auto", the default, picks whichever shows the image larger, so wide strips stack.
  - **?** lists the keys.
  - Click the lat or lon value to copy it; the Copy button copies both.

"Open in Scalabel" opens `label?project_name=…&task_index=…&item=…`. The `item` value counts from 1 within the task. The image number is its position in the export, which matches its position in the Scalabel project for an export made from that project.

## Tests

```bash
node --test tools/batch_review/tests/server.test.js
```
