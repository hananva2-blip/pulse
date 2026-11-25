# Static pages and a reverse proxy to the API; runs as the unprivileged nginx user (uid 101)
FROM nginxinc/nginx-unprivileged:1.30.6-alpine
# Take security fixes that came out after the base image was built
# (nginx comes from nginx.org; drop the modules we don't use so it can upgrade)
USER root
RUN apk upgrade --no-cache \
 && apk del nginx-module-geoip nginx-module-image-filter nginx-module-njs nginx-module-xslt \
 && apk add --no-cache --repository https://nginx.org/packages/alpine/v3.24/main "nginx>=1.30.8"
USER 101
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY site/ /usr/share/nginx/html/
ENV API_URL=http://api:8000
EXPOSE 8080
