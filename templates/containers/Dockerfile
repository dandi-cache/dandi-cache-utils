# syntax=docker/dockerfile:1

# The pinned runtime environment for this cache.
#
# Everything shared -- Debian, git-annex, the virtual environment, the `dandi_cache_utils` library,
# and the vendored `update_pipeline.sh` that CI extracts and runs -- comes from the base image, so
# a cache's Dockerfile is only the dependencies that are genuinely its own.
#
# Use `:nwb` instead of `:latest` if this cache streams remote NWB files; that tag already carries
# h5py, zarr, pynwb, hdmf-zarr, remfile, s3fs and the NWB Inspector.
FROM ghcr.io/dandi-cache/dandi-cache-utils:latest

LABEL org.opencontainers.image.source="https://github.com/dandi-cache/<cache-name>"
LABEL org.opencontainers.image.description="The pinned runtime environment for the DANDI Cache <cache-name> update pipeline."

# TODO: this cache's own processing dependencies, if any. Delete the line if there are none.
RUN pip install <dependency> <dependency>
