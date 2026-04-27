FROM node:22-slim AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY tsconfig.json ./
COPY src ./src
RUN npm run build

FROM node:22-slim
WORKDIR /app
COPY package*.json ./
RUN npm ci --omit=dev
COPY --from=build /app/dist ./dist
# Bundle NDLA-fagstoff (Helsefremmende arbeid HS-HEA vg2) — SQLite/FTS5
COPY ndla-scraper/data/ndla_helsefag.db ./dist/ndla_helsefag.db
# Bundle Felleskatalogen-doseringsdata (POC, ~18 flaggskip-legemidler)
COPY felleskatalogen-scraper/data/felleskatalogen.db ./dist/felleskatalogen.db
ENV PORT=3000
EXPOSE 3000
CMD ["node", "dist/index.js"]
