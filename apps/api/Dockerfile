# syntax=docker/dockerfile:1.7
#
# The YardLens API, which also processes the cameras. Build from the repository root:
#
#   docker build -f apps/api/Dockerfile -t yardlens .
#
# Inference runs on the CPU (PyTorch's CPU build plus OpenVINO), so the image stays small and runs
# on any x86-64 machine. To use a GPU, run the API outside Docker: see docs/deployment.md.

FROM python:3.14-slim-trixie AS build
COPY --from=ghcr.io/astral-sh/uv:0.11.28 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /src

# Dependencies first, so code changes reuse this layer. The lock is the single source of
# versions; PyTorch comes from its CPU-only index at the locked version, without the CUDA
# libraries (several gigabytes the container would never use).
COPY apps/api/pyproject.toml apps/api/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    set -eu; \
    uv venv /app/.venv; \
    uv export --frozen --no-dev --no-emit-project --no-hashes -o requirements.txt; \
    torch=$(sed -n "s/^torch==\([^ ;]*\) ; sys_platform != 'win32'$/\1/p" requirements.txt); \
    vision=$(sed -n "s/^torchvision==\([^ ;]*\) ; sys_platform != 'win32'$/\1/p" requirements.txt); \
    test -n "$torch" && test -n "$vision"; \
    uv pip install --python /app/.venv --no-deps \
        --index-url https://download.pytorch.org/whl/cpu \
        "torch==$torch" "torchvision==$vision"; \
    grep -vE '^(nvidia-(cu|nccl|nv)|triton==|cuda-)' requirements.txt > cpu.txt; \
    uv pip install --python /app/.venv -r cpu.txt

COPY apps/api/README.md ./
COPY apps/api/src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --python /app/.venv --no-deps .


FROM python:3.14-slim-trixie
# libgomp: OpenMP for PyTorch and OpenVINO; libglib: OpenCV.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 libglib2.0-0 ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home --shell /usr/sbin/nologin yardlens \
    && mkdir -p /data \
    && chown yardlens:yardlens /data

COPY --from=build /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    YARDLENS_DATA_DIR=/data \
    YARDLENS_HOST=0.0.0.0 \
    YARDLENS_LOG_JSON=true \
    YARDLENS_ENVIRONMENT=production \
    # Downloaded models and caches stay on the data volume.
    YOLO_CONFIG_DIR=/data/ultralytics \
    XDG_CACHE_HOME=/data/cache \
    HOME=/home/yardlens

WORKDIR /app
USER yardlens
VOLUME ["/data"]
EXPOSE 8100
# The schema is applied on every start (it is idempotent), then the API runs.
CMD ["sh", "-c", "db-setup && exec yardlens-api"]
