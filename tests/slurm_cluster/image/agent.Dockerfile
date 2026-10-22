# The agent container of the slurm_cluster tests: what DockerSandbox needs
# (bash, usermod, an `agent` user with a real HOME), process tools for signalling and the Python the
# single-file broker client runs on. No agent CLI is installed.
FROM ghcr.io/uw-syfi/vibesys/ci-base@sha256:cb33f7f771ecec2ef1225cf39b159591a040391bd73e4c01f00fdfc4c47611d2
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends procps \
 && rm -rf /var/lib/apt/lists/*
RUN useradd --uid 1000 --create-home --home-dir /home/agent --shell /bin/bash agent
USER agent
WORKDIR /home/agent
