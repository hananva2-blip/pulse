# Microsoft's Playwright image: Chromium and its system libraries, same version as the Python package
FROM mcr.microsoft.com/playwright/python:v1.64.1-noble
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
# PEP 668 protects a shared system Python; this image runs one process, so installing into it is fine
# Install, then remove pip, virtualenv and their cache: the runtime needs none of them, and they
# carry vendored copies of urllib3, certifi, msgpack and setuptools that Trivy flags as fixable
RUN pip3 install --no-cache-dir --break-system-packages --upgrade setuptools filelock \
 && pip3 install --no-cache-dir --break-system-packages -r requirements.txt \
 && pip3 uninstall -y --break-system-packages virtualenv pip \
 && rm -rf /root/.cache
COPY browser_runner.py .
# pwuser (uid 1001) comes with the image
USER pwuser
EXPOSE 9100
CMD ["python3", "browser_runner.py"]
