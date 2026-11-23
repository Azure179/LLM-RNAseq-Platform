# Shared local FastAPI/Streamlit runtime. No credentials or research data in layers.
FROM python:3.13.2-bookworm@sha256:4165118ed569aff9dbd11d5518199e5379d93bf5bf1cdda62eb13593cf66fb68

ENV LANG=C.UTF-8 LC_ALL=C.UTF-8 \
    PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    RNASEQ_WORKSPACE_ROOT=/workspace

# R is built explicitly: Debian's r-base would not preserve the audited R 4.6 line.
# Build tools remain in this first runtime image; size hardening is a later task.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl gfortran make g++ pkg-config cmake \
    libreadline-dev libpcre2-dev libbz2-dev liblzma-dev libcurl4-openssl-dev \
    libssl-dev libxml2-dev libicu-dev libcairo2-dev libpng-dev libjpeg62-turbo-dev \
    libtiff-dev libfontconfig1-dev libfreetype6-dev libharfbuzz-dev libfribidi-dev \
    libgit2-dev libxt-dev libblas-dev liblapack-dev libtirpc-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /tmp/r-build
RUN curl --fail --location --retry 3 https://cran.r-project.org/src/base/R-4/R-4.6.1.tar.gz -o R.tar.gz \
    && echo '4da6e61d2c0aac5f14a2e7e432cb5fcc269efe83da4293050ba7f03dff4e2cf4  R.tar.gz' | sha256sum --check - \
    && tar -xzf R.tar.gz --strip-components=1 \
    && ./configure --enable-R-shlib --with-x=no --with-cairo --with-blas --with-lapack \
    && make -j2 && make install \
    && cd /tmp && rm -rf /tmp/r-build

WORKDIR /app
# Current CRAN fs links system libuv; headers are required by the enrichment closure.
RUN apt-get update && apt-get install -y --no-install-recommends libuv1-dev \
    && rm -rf /var/lib/apt/lists/*
COPY container/install_r.R container/audit_runtime.R /opt/rnaseq/container/
COPY container/locks/r-linux.tsv container/locks/r_sources.py /opt/rnaseq/container/
RUN python /opt/rnaseq/container/r_sources.py /opt/rnaseq/container/r-linux.tsv /tmp/locked-r/src/contrib \
    && Rscript --vanilla /opt/rnaseq/container/install_r.R /opt/rnaseq/container/r-linux.tsv /tmp/locked-r \
    && Rscript --vanilla /opt/rnaseq/container/audit_runtime.R /opt/rnaseq/r-runtime.json \
    && rm -rf /tmp/locked-r

COPY container/python-constraints.txt /opt/rnaseq/container/python-constraints.txt
RUN python -m pip install --require-hashes --only-binary=:all: --no-deps --no-cache-dir -r /opt/rnaseq/container/python-constraints.txt \
    && python -m pip check
COPY container/locks/build-requirements.txt /opt/rnaseq/container/
RUN python -m venv /opt/rnaseq/build-env \
    && /opt/rnaseq/build-env/bin/pip install --require-hashes --only-binary=:all: --no-deps -r /opt/rnaseq/container/build-requirements.txt
COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/
RUN /opt/rnaseq/build-env/bin/pip wheel --no-deps --no-build-isolation -w /tmp/wheels . \
    && python -m pip install --no-index --no-deps /tmp/wheels/*.whl && rm -rf /tmp/wheels \
    && python -m pip check \
    && python -c "from bulk_rnaseq.builtin_registry import create_default_registry; assert 'rnaseq.go' in create_default_registry().list_skills()" \
    && python -m pip freeze > /opt/rnaseq/python-runtime.txt
COPY container/locks/verify_runtime.py /opt/rnaseq/container/
RUN python /opt/rnaseq/container/verify_runtime.py

COPY container/smoke_runtime.py container/runtime_checks.py container/workspace_roundtrip.py /opt/rnaseq/container/
COPY app/ ./app/
RUN groupadd --gid 10001 rnaseq \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin rnaseq \
    && mkdir -p /workspace \
    && chown 10001:10001 /workspace
USER 10001:10001
VOLUME ["/workspace"]
EXPOSE 8000 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=3).close()"]
CMD ["python", "-m", "uvicorn", "bulk_rnaseq.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
