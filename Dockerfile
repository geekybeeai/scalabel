FROM node:20-bookworm-slim

EXPOSE 8686

WORKDIR /opt/scalabel

# Annotation auto-correct runs in a Node child process
# (app/dist/annotation_fix_worker.js) using sharp's prebuilt libvips binary,
# which npm fetches as a platform-specific optional dependency. No Python is
# needed in this image.

COPY package*.json ./
RUN if [ -f package-lock.json ]; then npm ci --ignore-scripts; else npm install --ignore-scripts; fi

COPY . .

RUN npm run build && rm -f app/dist/tsconfig.tsbuildinfo

# Fail the build rather than ship an image whose auto-correction silently skips.
RUN node app/dist/annotation_fix_worker.js --preflight

CMD ["node", "--max-old-space-size=8192", "app/dist/main.js", "--config", "./local-data/scalabel/config.yml"]
