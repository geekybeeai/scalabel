FROM node:18-bookworm-slim

EXPOSE 8686

WORKDIR /opt/scalabel

# Python is a RUNTIME dependency, not a build one: the server spawns
# `python3 -m annotation_fix.stdio` to auto-correct annotations at project
# creation. Without it the correction skips SILENTLY — projects are still
# created, just with uncorrected annotations — so it must be in the image.
# pillow/numpy/scipy come from the distro rather than pip because the slim
# image has no compiler toolchain and scipy has no manylinux wheel for every
# platform (notably linux/arm64), where pip would try to build from source.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 \
        python3-numpy \
        python3-scipy \
        python3-pil \
    && rm -rf /var/lib/apt/lists/*

COPY package*.json ./
RUN if [ -f package-lock.json ]; then npm ci --ignore-scripts; else npm install --ignore-scripts; fi

COPY . .

RUN npm run build && rm -f app/dist/tsconfig.tsbuildinfo

# Fail the build rather than ship an image whose auto-correction silently skips.
RUN python3 tools/annotation_fix/preflight.py --build

CMD ["node", "--max-old-space-size=8192", "app/dist/main.js", "--config", "./local-data/scalabel/config.yml"]
