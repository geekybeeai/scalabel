FROM node:18-bullseye-slim

EXPOSE 8686

WORKDIR /opt/scalabel

COPY package*.json ./
RUN if [ -f package-lock.json ]; then npm ci --ignore-scripts; else npm install --ignore-scripts; fi

COPY . .

RUN npm run build && rm -f app/dist/tsconfig.tsbuildinfo

CMD ["node", "--max-old-space-size=8192", "app/dist/main.js", "--config", "./local-data/scalabel/config.yml"]
