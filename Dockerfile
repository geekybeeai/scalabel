FROM node:18-bookworm-slim

EXPOSE 8686

WORKDIR /opt/scalabel

# Annotation auto-correction used to spawn `python3 -m annotation_fix.stdio`,
# which made python3 plus pillow/numpy/scipy a RUNTIME dependency of this image.
# It is now TypeScript running in a worker thread (app/src/server/annotation_fix)
# with no dependency beyond Node's own zlib, so none of that is installed any
# more. tools/annotation_fix is still in the tree and still works as a standalone
# CLI, but nothing in the server calls it and it needs no support here.

# Redis IS required. The server spawns `redis-server` itself on startup
# (launchRedisServer in main.ts) and the client connects over 127.0.0.1, so it
# has to live in this image rather than in a sibling container. Without it the
# spawn fails with ENOENT, which is only logged — but every read that goes
# through the cache then never settles, so /dashboard and /postTaskMetaData hang
# and the dashboard spins forever.
RUN apt-get update \
    && apt-get install -y --no-install-recommends redis-server \
    && rm -rf /var/lib/apt/lists/*

COPY package*.json ./
RUN if [ -f package-lock.json ]; then npm ci --ignore-scripts; else npm install --ignore-scripts; fi

COPY . .

RUN npm run build && rm -f app/dist/tsconfig.tsbuildinfo

# Fail the build rather than ship an image whose auto-correction silently skips.
# --build skips the checks that need the data volume, which is not mounted yet.
RUN node app/dist/annotation_fix_preflight.js --build

CMD ["node", "--max-old-space-size=8192", "app/dist/main.js", "--config", "./local-data/scalabel/config.yml"]
