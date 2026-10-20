# Batch Review

A web page for reviewing Scalabel exports one batch at a time, at http://localhost:8687:

- the original image and the annotated image side by side, in Scalabel's colours, with zoom and pan kept in step;
- Scalabel-style navigation: previous / next, a number box to jump, and a slider;
- "Open in Scalabel" on the same image;
- the image's lat / lon (copyable), with Google Maps and Street View links;
- a link to the image's CAD picture;
- a checkbox per class, plus line width, opacity and brightness controls.

## Setup (any system: Windows, macOS, Linux)

It needs only **Node.js 16 or newer**, which Scalabel already needs. There is nothing to install: no `npm install`, no Python.

1. Copy the `batch_review` folder anywhere.
2. Copy `config.example.json` to `config.json` and set the paths (see below).
3. Run `node server.js` inside the folder.
4. Open http://localhost:8687.

```bash
cd batch_review
node server.js                      # uses config.json in this folder
node server.js --config other.json  # another config
node server.js --port 8690          # another port
```

The first line it prints tells you how many batches, lat/lon rows and CAD images it found, followed by any warnings (for example, a path that does not exist). To stop it, press Ctrl+C.

## config.json

A relative path is resolved from the folder that holds the config file. On Windows, write paths with `/`, as in `C:/scalabel/local-data`, or escape backslashes as `C:\\scalabel\\local-data`.

| Key | What it is |
|---|---|
| `batches_dir` | Folder of Scalabel export `.json` files. Each file is one batch in the Batch list. |
| `batches` | Optional extra export files, given one by one. |
| `image_root` | Folder that the frames' `url` / `name` paths start from. For Scalabel's local storage, this is the data folder that contains `items/`, e.g. `.../local-data`. A frame whose url is already `http(s)://…` is loaded from that url. |
| `cad_dir` | Folder of CAD images, searched at any depth. |
| `geo_csv` | A CSV file, or a list of them, holding image name, lat and lon (see below). |
| `scalabel_url` | Scalabel server used by "Open in Scalabel" and "Live Scalabel", e.g. `http://localhost:8686`. |
| `task_size` | Scalabel project task size (50 for these projects). Used to work out the image's task and item. |
| `projects` | Optional `{"export file name": "Scalabel project name"}` map. Without it, the project name is the file name before `_export_`, which is Scalabel's export naming. |
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

- **Export file / Live Scalabel:** Export file shows the labels in the export file. Live Scalabel fetches the project's current labels from Scalabel (`/getExport`). If Scalabel can't be reached, the page goes back to the file and says so.
- **Reload:** re-reads the batches folder, the CSV and the CAD folder without restarting the server.
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
  | Space | play / pause |
  | B | hide / show the side panel |
  | V | next layout |
  | ? | help |

- **Image bar (bottom), as in Scalabel:**
  - The images change while you drag the slider, and a label above it shows the number and name.
  - Scrolling over the bar, or a trackpad side-swipe, steps through the images.
  - Type a number to jump to that image.
  - **Play** (or **Space**) moves to the next image at the chosen speed and stops at the end.
- **Mouse:** scroll to zoom, drag to pan, double-click to zoom in. Either image can be used; both move together.
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
