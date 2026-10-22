# Build stage: compile wheels so the final image carries no build tools
FROM python:3.12-slim AS build
WORKDIR /build
COPY requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir /wheels/* && rm -rf /wheels \
 && useradd --create-home --uid 1000 pulse
COPY runner.py .
USER pulse
EXPOSE 9100
CMD ["python", "runner.py"]
