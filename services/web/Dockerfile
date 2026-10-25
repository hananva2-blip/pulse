# Static pages and a reverse proxy to the API; runs as the unprivileged nginx user (uid 101)
FROM nginxinc/nginx-unprivileged:1.29.8-alpine
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY site/ /usr/share/nginx/html/
ENV API_URL=http://api:8000
EXPOSE 8080
