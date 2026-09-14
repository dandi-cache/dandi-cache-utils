#!/usr/bin/env bash
#
# The DANDI cache update pipeline: one orchestrator for every cache in the organization.
#
# This script is vendored inside the runtime container image at /opt/dandi-cache-utils/bin/ and
# extracted from it at run time, so the image digest pins the orchestration and the runtime
# environment together. There is no separate version to keep in sync.
#
# Everything that used to differ between repositories -- the number of input subdatasets, the
# output file names, the entry point, the batch size -- is read from the cache's own `cache.toml`
# rather than written into the script. A cache with no inputs and a cache with three run the same
# code path here.
#
#   - `main`        holds only the code, the config, and the CI (this checkout).
#   - `derivatives` is a persistent DataLad dataset on its own branch, cloned standalone into
#                   scratch. The processing is recorded there with `datalad containers-run`, so
#                   every update carries full provenance: the command, the input subdataset
#                   commits, the output diff, the container image digest, and the run's own log
#                   under `logs/`.
#   - `dist`        is the lightweight, force-recreated publication artifact consumed by
#                   downstream users.
#
# The published image is used purely as the runtime environment: the code and the dataset are
# bind-mounted in, and only the image digest is stored in the dataset (a small text file), so the
# dataset stays annex-free and ghcr holds the bytes.
#
# Required environment variables:
#   REPO_URL     Authenticated https remote for the cache repository (clone/push).
#   WORKSPACE    Path to the `main` checkout that holds the code and `cache.toml`.
#   IMAGE        Container image reference to run the processing in.
# Optional:
#   OPERATION    Which entry point from `[operations]` to run (default: `update`).
#   TESTING      "true" runs the operation in testing mode: a handful of items, written to
#                `testing_`-prefixed files, leaving the real cache untouched.
#   LIMIT        Cap on the number of new items processed this run. Falls back to the operation's
#                `limit` in `cache.toml`.
#   GITHUB_SHA   Recorded in the provenance message to link results to the code commit.
#   RUNNER_TEMP  Scratch directory for the working clones (default: /tmp).
#   DANDI_CACHE_UTILS_DIR  Where this script and its sources were extracted to (default: the
#                directory containing this script's parent).
set -euo pipefail

: "${REPO_URL:?REPO_URL must be set}"
: "${WORKSPACE:?WORKSPACE must be set}"
IMAGE="${IMAGE:-}"  # Defaults below to the image `cache.toml` declares for this cache.
OPERATION="${OPERATION:-update}"
TESTING="${TESTING:-}"
LIMIT="${LIMIT:-}"
GITHUB_SHA="${GITHUB_SHA:-unknown}"

BOT_NAME="github-actions[bot]"
BOT_EMAIL="github-actions[bot]@users.noreply.github.com"

SCRIPT_DIRECTORY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UTILS_DIR="${DANDI_CACHE_UTILS_DIR:-$(dirname "${SCRIPT_DIRECTORY}")}"

DS="${RUNNER_TEMP:-/tmp}/derivatives-dataset"
DISTDIR="${RUNNER_TEMP:-/tmp}/dist-publish"
RUNNER_VENV="${RUNNER_TEMP:-/tmp}/pipeline-venv"

# ---------------------------------------------------------------------------------------------
# Transient-failure handling.
#
# Several steps occasionally fail for reasons that have nothing to do with the operation itself:
# GitHub's git backend rejecting a push with "fatal error in commit_refs" or "cannot lock ref
# 'refs/heads/<branch>': is at <X> but expected <Y>", or ghcr.io answering a manifest request with
# a transient "denied" right after a successful login. Retry a few times before giving up, so a
# one-off hiccup does not fail a run that may have taken hours.
# ---------------------------------------------------------------------------------------------
retry_with_backoff() {
  local label="$1"
  shift
  local attempt=1
  local max_attempts=5
  local delay=5
  while ! "$@"; do
    if [ "${attempt}" -ge "${max_attempts}" ]; then
      echo "ERROR: '${label}' failed after ${attempt} attempts." >&2
      return 1
    fi
    echo "WARNING: '${label}' failed (attempt ${attempt}/${max_attempts}); retrying in ${delay}s." >&2
    sleep "${delay}"
    attempt=$((attempt + 1))
    delay=$((delay * 2))
  done
}

# Push a local ref to a remote branch, retrying on transient failures and reconciling the case
# where the push actually landed.
#
# The "cannot lock ref" rejection deserves the extra check: when <X> is the commit this run just
# created, the push was applied server-side and GitHub reported a failure anyway. Comparing the
# remote's ref against what we are pushing detects exactly that, so the run proceeds to publish
# `dist` instead of leaving it stale. A genuine non-fast-forward still fails once retries run out.
#
#   push_with_retry <repo-dir> <remote-branch> <local-ref> [extra git-push args...]
push_with_retry() {
  local repo_dir="$1" branch="$2" local_ref="$3"
  shift 3
  local attempt=1
  local max_attempts=5
  local delay=5
  local want remote_sha
  want=$(git -C "${repo_dir}" rev-parse "${local_ref}")

  while true; do
    if git -C "${repo_dir}" push "$@" "${REPO_URL}" "${local_ref}:${branch}"; then
      return 0
    fi

    remote_sha=$(git ls-remote --heads "${REPO_URL}" "${branch}" | cut -f1 || true)
    if [ -n "${remote_sha}" ] && [ "${remote_sha}" = "${want}" ]; then
      echo "Remote '${branch}' is already at ${want}; the push landed despite the reported failure."
      return 0
    fi

    if [ "${attempt}" -ge "${max_attempts}" ]; then
      echo "ERROR: push of '${branch}' failed after ${attempt} attempts." >&2
      return 1
    fi
    echo "WARNING: push of '${branch}' failed (attempt ${attempt}/${max_attempts}); retrying in ${delay}s." >&2
    sleep "${delay}"
    attempt=$((attempt + 1))
    delay=$((delay * 2))
  done
}

# ---------------------------------------------------------------------------------------------
# Read the cache's declarative configuration.
#
# The same `cache.toml` the update code reads is parsed here, with the runner's bare `python3`
# against the sources vendored beside this script. That is why `dandi_cache_utils.config` imports
# nothing outside the standard library: at this point in the run no environment exists yet, so the
# module is run as a plain script rather than through the `dandi-cache` command, which needs one.
# ---------------------------------------------------------------------------------------------
CONFIG_FILE="${WORKSPACE}/cache.toml"
if [ ! -f "${CONFIG_FILE}" ]; then
  echo "ERROR: no cache.toml at ${CONFIG_FILE}. Every cache declares its inputs and outputs there." >&2
  exit 1
fi

config_as_shell() { python3 "${UTILS_DIR}/src/dandi_cache_utils/config.py" "$@"; }

# Everything after the runner's environment is built goes through the installed command instead.
dandi_cache() { "${RUNNER_VENV}/bin/dandi-cache" "$@"; }

# Declared up front so that a malformed config fails the `:?` checks below rather than leaving a
# stale or unset variable to surface much later, and so shellcheck can see them assigned. The eval
# also sets CACHE_FILE_STEM and OPERATION_NAME, which are part of the shared config contract but
# are not needed by this script.
CACHE_NAME=""
CACHE_IMAGE=""
CACHE_OUTPUTS=()
INPUT_PATHS=()
INPUT_URLS=()
INPUT_BRANCHES=()
OPERATION_SCRIPT=""
OPERATION_LABEL=""
OPERATION_DEFAULT_LIMIT=""

eval "$(config_as_shell "${CONFIG_FILE}" --operation "${OPERATION}")"
: "${CACHE_NAME:?cache.toml did not yield a cache name}"
: "${OPERATION_SCRIPT:?cache.toml did not yield an entry point for operation ${OPERATION}}"

LIMIT="${LIMIT:-${OPERATION_DEFAULT_LIMIT}}"
# The image is a convention of the cache name, so the workflow does not have to repeat it.
IMAGE="${IMAGE:-${CACHE_IMAGE}:latest}"

echo "Cache:     ${CACHE_NAME}"
echo "Image:     ${IMAGE}"
echo "Operation: ${OPERATION_LABEL} (${OPERATION_SCRIPT})"
echo "Inputs:    ${#INPUT_PATHS[@]}"
echo "Outputs:   ${CACHE_OUTPUTS[*]}"

# Build the argument string for the entry point. `containers-run` takes the command as one string,
# so this is a string rather than an array. Testing mode and the batch cap compose: the Python side
# lets testing win on size, so a smoke run stays small whatever the cache's batch size is.
RUN_ARGUMENTS=""
if [ "${TESTING}" = "true" ]; then
  RUN_ARGUMENTS="${RUN_ARGUMENTS} --testing"
fi
if [ -n "${LIMIT}" ]; then
  RUN_ARGUMENTS="${RUN_ARGUMENTS} --limit ${LIMIT}"
fi

# ---------------------------------------------------------------------------------------------
# The runner's own environment: datalad and the container extension, pinned beside this script,
# plus the vendored library itself so the `dandi-cache` command is available here. The cache
# repositories no longer carry any of these as dependencies of their processing environment.
# ---------------------------------------------------------------------------------------------
if [ ! -x "${RUNNER_VENV}/bin/datalad" ] || [ ! -x "${RUNNER_VENV}/bin/dandi-cache" ]; then
  uv venv "${RUNNER_VENV}"
  uv pip install --python "${RUNNER_VENV}/bin/python" \
    -r "${SCRIPT_DIRECTORY}/runner-requirements.txt" "${UTILS_DIR}"
fi
datalad() { "${RUNNER_VENV}/bin/datalad" "$@"; }

git config --global user.name "${BOT_NAME}"
git config --global user.email "${BOT_EMAIL}"

# The `derivatives` dataset is a standalone clone (not a git worktree): datalad writes each input
# subdataset's config into `.git/config`, which is a file -- not a directory -- in a worktree, so
# subdataset registration fails there.
rm -rf "${DS}" "${DISTDIR}"

# ---------------------------------------------------------------------------------------------
# Reuse the persistent `derivatives` dataset branch, or bootstrap a new one.
# ---------------------------------------------------------------------------------------------
if git ls-remote --heads "${REPO_URL}" derivatives | grep -q refs/heads/derivatives; then
  echo "Reusing the existing 'derivatives' dataset branch."
  git clone --branch derivatives --single-branch "${REPO_URL}" "${DS}"
else
  echo "Bootstrapping a new 'derivatives' DataLad dataset."
  datalad create --no-annex "${DS}"
  datalad save -d "${DS}" -m "Initialize derivatives dataset"
fi

# Establish the dataset as the working directory for every operation that follows. All subsequent
# dataset paths are dataset-relative from here, so a `datalad save`/`status` argument can never
# resolve against WORKSPACE (the code checkout) and silently fall outside the dataset. This is the
# only `cd` in the script.
cd "${DS}"

git config user.name "${BOT_NAME}"
git config user.email "${BOT_EMAIL}"
mkdir -p derivatives logs

# Write the study-level BIDS dataset_description.json onto the derivatives dataset so the published
# dataset is self-describing. It is rendered from `cache.toml` rather than copied from a file each
# repository maintained by hand. No `|| true` mask, so a genuine save failure fails the run loudly
# (`datalad save` already exits 0 when there is nothing to save).
dandi_cache dataset-description "${CONFIG_FILE}" --output dataset_description.json
datalad save -m "Update dataset_description.json" dataset_description.json

# ---------------------------------------------------------------------------------------------
# Advance every declared input subdataset to the tip of the branch it publishes on.
#
# Registration is idempotent: an input added to a cache that already has a `derivatives` branch is
# cloned on first sight rather than failing. The tracking branch is set explicitly on every run and
# the submodule is only ever updated with `--remote`, so the previously recorded commit is never
# fetched -- an upstream cache may have rewritten its history, and fetching a commit that no longer
# exists on the remote fails the run with "not our ref".
# ---------------------------------------------------------------------------------------------
if [ "${#INPUT_PATHS[@]}" -gt 0 ]; then
  for index in "${!INPUT_PATHS[@]}"; do
    input_path="${INPUT_PATHS[${index}]}"
    input_url="${INPUT_URLS[${index}]}"
    input_branch="${INPUT_BRANCHES[${index}]}"

    if ! git config -f .gitmodules --get "submodule.${input_path}.url" > /dev/null 2>&1; then
      echo "Registering new input subdataset ${input_path}."
      datalad clone -d . "${input_url}" "${input_path}"
    fi

    git submodule set-branch --branch "${input_branch}" -- "${input_path}"
    git submodule update --init --remote "${input_path}"
  done

  # `-d .` is required: without it, `datalad save` resolves the target dataset by walking up from
  # the given path, and since that path is itself a subdataset mount point it silently targets the
  # (clean, nothing-to-save) subdataset instead of registering the new commit in the superdataset
  # -- exiting 0 without having saved anything.
  datalad save -d . -m "Update input subdatasets to latest" "${INPUT_PATHS[@]}" .gitmodules
fi

# ---------------------------------------------------------------------------------------------
# Pin the published image digest and register it as a container. Only the digest is stored (a
# small text file), so the dataset stays annex-free; ghcr holds the image bytes.
# ---------------------------------------------------------------------------------------------
retry_with_backoff "docker pull ${IMAGE}" docker pull "${IMAGE}"
DIGEST=$(docker inspect --format '{{index .RepoDigests 0}}' "${IMAGE}")
mkdir -p .datalad/environments/pipeline
printf '%s\n' "${DIGEST}" > .datalad/environments/pipeline/image

# The {img}/{cmd} placeholders and the $-expansions are interpolated by datalad at run time, not by
# this shell, so they are intentionally left unexpanded here. The whole code checkout is mounted
# (not just `code/`) so the container finds `cache.toml` next to the entry point.
# shellcheck disable=SC2016
datalad containers-add pipeline --update \
  --image .datalad/environments/pipeline/image \
  --call-fmt 'docker run --rm -u "$(id -u):$(id -g)" -e HOME=/tmp -v "$PWD":/tmp -w /tmp -v "$WORKSPACE":/workspace:ro "$(cat {img})" {cmd}'
datalad save -m "Pin runtime container image to ${DIGEST}" .datalad

# Fail fast if the dataset is not clean before the recorded run. `containers-run` requires a clean
# tree to detect the command's changes and otherwise aborts with a generic "clean dataset required"
# error; surfacing the offending paths here is far easier to diagnose. The JSON renderer is
# required: the default renderer prints "nothing to save, working tree clean" on a clean dataset,
# and the JSON one also emits records for clean paths, so filter to the non-clean states.
DATASET_STATUS=$(datalad -f json status | jq -r 'select(.state != "clean") | "\(.state): \(.path)"')
if [ -n "${DATASET_STATUS}" ]; then
  echo "ERROR: derivatives dataset is not clean before containers-run." >&2
  echo "Offending paths:" >&2
  echo "${DATASET_STATUS}" >&2
  exit 1
fi

# ---------------------------------------------------------------------------------------------
# Run the processing inside the published image.
#
# `--explicit` keeps datalad from clearing the outputs first, which is required because a cache's
# previous output is the input of its next incremental run. Each declared input subdataset is
# pinned with `--input`, so every result records the exact upstream commits it was computed from; a
# first-in-chain cache declares none and fetches its own inputs over the network instead.
#
# `logs` is a second output: the operation writes a timestamped log of each run there, so the log
# of every completed update is committed to `derivatives` with the results it produced. Only
# `derivatives` is published to `dist`.
# ---------------------------------------------------------------------------------------------
RUN_INPUT_ARGS=()
for input_path in ${INPUT_PATHS[@]+"${INPUT_PATHS[@]}"}; do
  RUN_INPUT_ARGS+=(--input "${input_path}")
done

datalad containers-run -n pipeline --explicit \
  ${RUN_INPUT_ARGS[@]+"${RUN_INPUT_ARGS[@]}"} \
  --output derivatives \
  --output logs \
  -m "${OPERATION_LABEL} ${CACHE_NAME} (code @ ${GITHUB_SHA}; image ${DIGEST})" \
  "python /workspace/${OPERATION_SCRIPT} --base-directory /tmp${RUN_ARGUMENTS}"

# Publish the full results to the `derivatives` branch.
push_with_retry "${DS}" derivatives HEAD

# ---------------------------------------------------------------------------------------------
# Build and force-publish the consumer-facing `dist` artifact from a fresh repository.
#
# Only the files `cache.toml` declares as outputs are published. That is what keeps a `testing_`
# artifact left by a smoke run from ever reaching consumers, and it replaces the guesswork of
# globbing `derivatives/*.jsonl.gz`.
# ---------------------------------------------------------------------------------------------
dandi_cache compress --base-directory "${DS}"
mkdir -p "${DISTDIR}/derivatives"

published=0
for output in "${CACHE_OUTPUTS[@]}"; do
  if [ -f "${DS}/derivatives/${output}.gz" ]; then
    cp "${DS}/derivatives/${output}.gz" "${DISTDIR}/derivatives/"
    published=$((published + 1))
  else
    echo "WARNING: declared output ${output}.gz was not produced; not publishing it." >&2
  fi
done

if [ "${published}" -eq 0 ]; then
  echo "No declared outputs were produced, so 'dist' is left as it was."
  exit 0
fi

cp "${DS}/dataset_description.json" "${DISTDIR}/dataset_description.json"
git -C "${DISTDIR}" init -q -b dist
git -C "${DISTDIR}" config user.name "${BOT_NAME}"
git -C "${DISTDIR}" config user.email "${BOT_EMAIL}"
git -C "${DISTDIR}" add dataset_description.json derivatives
git -C "${DISTDIR}" commit -q -m "Publish ${CACHE_NAME}"
push_with_retry "${DISTDIR}" dist dist -f
