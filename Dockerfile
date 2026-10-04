# syntax=docker/dockerfile:1.7

ARG JUPYTER_BASE_IMAGE=quay.io/jupyter/minimal-notebook:2026-09-01@sha256:aebcf531fc77f3341568f5e37de7eb392ae48f1ae5ce9bc9cf779bd602548d17
FROM ${JUPYTER_BASE_IMAGE}

USER 0

# The distro repository is fixed by the dated parent image; these runtime
# utilities receive security updates when the base image is intentionally bumped.
# hadolint ignore=DL3008
RUN DEBIAN_FRONTEND=noninteractive apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install --yes --no-install-recommends \
        netcat-openbsd \
        tshark \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.12.17@sha256:10787c682e4184e4f290de1171fd4703dc63de99221f10fe1c99002ce7fa9acc /uv /uvx /bin/
COPY requirements.lock /tmp/requirements.lock
RUN uv pip install \
        --system \
        --no-cache-dir \
        --require-hashes \
        --constraint /tmp/requirements.lock \
        --requirement /tmp/requirements.lock \
    && uv pip uninstall --system jupyterhub \
    && rm -f /usr/local/bin/start-singleuser.py /usr/local/bin/start-singleuser.sh \
    && rm /tmp/requirements.lock \
    && fix-permissions "${CONDA_DIR}" \
    && fix-permissions "/home/${NB_USER}"

COPY --chown=${NB_UID}:${NB_GID} ipython_startup/ /opt/cms-labs/ipython_startup/
COPY --chown=${NB_UID}:${NB_GID} cms_labs_jupyter/ /opt/cms-labs/python/cms_labs_jupyter/
COPY --chmod=755 cms_labs_jupyter/traffic_capture.py /usr/local/bin/cms-labs-pcap
COPY --chmod=755 notebook.entrypoint.sh /usr/local/bin/notebook.entrypoint.sh
RUN ln -s /usr/local/bin/notebook.entrypoint.sh \
        /usr/local/bin/start-notebook.d/10-cms-labs.sh

USER 1000

ENV PYTHONPATH=/opt/cms-labs/python

EXPOSE 8888

# The parent image keeps start.sh as ENTRYPOINT. Clabgate may append
# ServerApp.base_url and authentication arguments to this command.
CMD ["start-notebook.py"]
