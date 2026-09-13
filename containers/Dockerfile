# syntax=docker/dockerfile:1

# The shared base image for every DANDI cache.
#
# This is how `dandi-cache-utils` is distributed. It is not published to PyPI: it is vendored into
# this image, and each cache's own image is built `FROM` it. A cache image therefore carries, at
# one pinned digest:
#
#   - the Python library (`dandi_cache_utils`) that its `code/update.py` imports;
#   - the orchestration script (`bin/update_pipeline.sh`) that CI extracts and runs on the runner;
#   - the runner's own pinned requirements, so the orchestration environment is pinned with it.
#
# That last point is the reason the script is vendored here rather than checked out separately:
# the image digest recorded in each run's provenance pins the orchestration and the runtime
# together, so a recorded run can be reproduced from the digest alone.
#
# The loose dependencies are resolved fresh at build time and the resulting image (by digest) is
# the lock, so there is no lockfile in the repository to hold in sync.
#
# NeuroDebian trixie (Debian 13) is the base layer: it provides Python 3.13 and a recent
# git-annex-standalone, which DataLad requires for the annex-backed dataset and its provenance.
FROM neurodebian:trixie AS core

LABEL org.opencontainers.image.source="https://github.com/dandi-cache/dandi-cache-utils"
LABEL org.opencontainers.image.description="The shared runtime environment and orchestration for every DANDI Cache."

# git-annex (for DataLad / annex provenance), Python, and the basics for cloning and TLS.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        git \
        git-annex-standalone \
        python3 \
        python3-pip \
        python3-venv \
    && rm -rf /var/lib/apt/lists/*

ENV VIRTUAL_ENV=/opt/venv \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Debian's system interpreter is externally managed (PEP 668), so install into a dedicated virtual
# environment rather than forcing pip with --break-system-packages. This also keeps our
# dependencies cleanly separated from the apt-managed Python packages.
RUN python3 -m venv "${VIRTUAL_ENV}"
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"

# The vendored tree. CI extracts this whole directory from the image and runs the pipeline from it,
# so the sources are present as files as well as installed into the environment.
ENV DANDI_CACHE_UTILS_DIR=/opt/dandi-cache-utils
COPY pyproject.toml README.md ${DANDI_CACHE_UTILS_DIR}/
COPY src ${DANDI_CACHE_UTILS_DIR}/src
COPY bin ${DANDI_CACHE_UTILS_DIR}/bin
RUN chmod +x ${DANDI_CACHE_UTILS_DIR}/bin/*.sh

# `s3` and `archive` are cheap and needed by most caches; the heavy NWB stack is the `nwb` stage.
RUN pip install "${DANDI_CACHE_UTILS_DIR}[s3,archive]"

CMD ["python", "--version"]


# The NWB-reading variant, published as the `nwb` tag.
#
# Every `valid-nwb-file-to-*` cache streams remote NWB files and walks their structure, so they all
# need the same stack. Building it once here means those caches need no Dockerfile of their own
# beyond a `FROM` line.
FROM core AS nwb

LABEL org.opencontainers.image.description="The shared DANDI Cache runtime, with the remote NWB reading stack."

RUN pip install "${DANDI_CACHE_UTILS_DIR}[nwb]"

CMD ["python", "--version"]
