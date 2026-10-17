# syntax=docker/dockerfile:1
#
# TrafficFuse AI — reproducible lab image (Phase 1A.1, review finding P1).
#
# One shared image runs BOTH lab roles via explicit startup commands:
#   * FastAPI workload (healthy and faulty modes, port 8080, bound to 0.0.0.0):
#       python -m trafficfuse.lab.service
#   * in-cluster probe CLI (writes JSON evidence to --output and to stdout):
#       python -m trafficfuse.lab.probe_cli --url ... --output /evidence/probe.json
#
# The Agentic Intent Gate execution framework is deliberately NOT installed:
# the lab package does not import it (verified: no `agentic_intent_gate` import
# under src/trafficfuse/lab). The pinned consumer dependency stays in
# pyproject.toml / uv.lock for the application runtime, not for this image.
#
# Build context is the repository root (src/ is required). See .dockerignore.
#
#   docker build --pull=false -f deploy/lab/Dockerfile \
#       -t trafficfuse-lab-service:phase1a .
#   kind load docker-image trafficfuse-lab-service:phase1a --name trafficfuse-lab
#
# No Git credentials, cloud tokens or private configuration are embedded; the
# build needs nothing beyond the public base image and pinned PyPI versions.

# Base image pinned by digest (verified locally 2026-10-18: python 3.13.7,
# linux/amd64). A `python:3.13-slim` tag reference is intentionally avoided:
# moving tags are not reproducible. Switching base images requires re-pinning
# the digest and re-running the offline build validation.
ARG PYTHON_IMAGE=python@sha256:5f55cdf0c5d9dc1a415637a5ccc4a9e18663ad203673173b8cda8f8dcacef689

FROM ${PYTHON_IMAGE} AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Exact pins mirror uv.lock (authoritative lockfile; `uv lock --check` must
# stay clean). Transitive versions are pinned to keep the image reproducible.
COPY deploy/lab/requirements-lab.txt /tmp/requirements-lab.txt
RUN pip install --no-cache-dir --requirement /tmp/requirements-lab.txt \
    && rm /tmp/requirements-lab.txt

# Application code: only the lab package tree is needed by this image.
WORKDIR /app
COPY src/trafficfuse ./trafficfuse

# Run as a non-root user where practical.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin trafficfuse \
    && chown -R trafficfuse:trafficfuse /app
USER trafficfuse

# The lab service binds 0.0.0.0 inside the container (see lab/service.py
# _main: host="0.0.0.0"); 8080 matches the Kubernetes manifests.
EXPOSE 8080

CMD ["python", "-m", "trafficfuse.lab.service"]
