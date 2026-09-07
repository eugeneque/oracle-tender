# Сборка фронтенда и отдача статики nginx'ом. Vite-приложение нельзя «просто открыть» —
# TS/JSX собираются заранее, в браузер попадает уже готовый бандл из dist/.
FROM node:20-alpine AS build

WORKDIR /app

# Слой зависимостей отдельно от исходников: правка компонента не пересобирает npm ci.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build

FROM nginx:1.27-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
