FROM node:22-alpine AS build
WORKDIR /web
COPY package.json package-lock.json* ./
RUN npm ci
COPY . .
ARG VITE_API_BASE_URL=""
ARG VITE_AMAP_JS_KEY=""
ARG VITE_AMAP_JS_SECURITY_CODE=""
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL VITE_AMAP_JS_KEY=$VITE_AMAP_JS_KEY VITE_AMAP_JS_SECURITY_CODE=$VITE_AMAP_JS_SECURITY_CODE
RUN npm run build

FROM nginx:1.29-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /web/dist /usr/share/nginx/html
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=3s CMD wget -q -O- http://127.0.0.1/healthz || exit 1
