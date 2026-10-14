# syntax=docker/dockerfile:1.7
#
# The YardLens core: the worker (`yardlens-worker`, the default command) and the API
# (`yardlens-api`, which Docker Compose runs). Build from the repository root:
#
#   docker build -f apps/api/Dockerfile --build-arg VARIANT=cpu -t yardlens:cpu .
#
# VARIANT=cpu   PyTorch for the CPU plus OpenVINO: small, runs on any x86-64 machine.
# VARIANT=cuda  PyTorch with CUDA for NVIDIA GPUs: large; run it with `--gpus all`.

ARG VARIANT=cpu

FROM python:3.14-slim-trixie AS build
ARG VARIANT
COPY --from=ghcr.io/astral-sh/uv:0.11.28 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /src

# Dependencies first, so code changes reuse this layer. The lock is the single source of
# versions; the CPU variant takes the same PyTorch release from PyTorch's CPU-only index and
# leaves out the CUDA libraries (several gigabytes it would never use).
COPY apps/api/pyproject.toml apps/api/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    set -eu; \
    uv venv /app/.venv; \
    uv export --frozen --no-dev --no-emit-project --no-hashes -o requirements.txt; \
    if [ "$VARIANT" = "cpu" ]; then \
        torch=$(sed -n "s/^torch==\([^ ;]*\) ; sys_platform != 'win32'$/\1/p" requirements.txt); \
        vision=$(sed -n "s/^torchvision==\([^ ;]*\) ; sys_platform != 'win32'$/\1/p" requirements.txt); \
        test -n "$torch" && test -n "$vision"; \
        uv pip install --python /app/.venv --no-deps \
            --index-url https://download.pytorch.org/whl/cpu \
            "torch==$torch" "torchvision==$vision"; \
        grep -vE '^(nvidia-(cu|nccl|nv)|triton==|cuda-)' requirements.txt > cpu.txt; \
        mv cpu.txt requirements.txt; \
    elif [ "$VARIANT" != "cuda" ]; then \
        echo "VARIANT must be cpu or cuda" >&2; exit 1; \
    fi; \
    uv pip install --python /app/.venv -r requirements.txt

COPY apps/api/README.md ./
COPY apps/api/src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --python /app/.venv --no-deps .


FROM python:3.14-slim-trixie
ARG VARIANT
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
    YARDLENS_VARIANT=$VARIANT \
    YARDLENS_DATA_DIR=/data \
    YARDLENS_HOST=0.0.0.0 \
    YARDLENS_LOG_JSON=true \
    YARDLENS_ENVIRONMENT=production \
    # Downloaded models and caches stay on the data volume.
    YOLO_CONFIG_DIR=/data/ultralytics \
    XDG_CACHE_HOME=/data/cache \
    HOME=/home/yardlens \
    # NVIDIA: compute for inference, video for hardware decoding (no effect without a GPU).
    NVIDIA_DRIVER_CAPABILITIES=compute,video,utility

WORKDIR /app
USER yardlens
VOLUME ["/data"]
EXPOSE 8100
CMD ["yardlens-worker"]
