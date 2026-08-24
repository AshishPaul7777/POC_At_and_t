# Backend image: FastAPI + the Salesforce CLI.
#
# The sf CLI is not optional garnish -- the retrieve stage and the delete
# rehearsal both shell out to it -- and it is a Node program. So this image
# carries a Python runtime and a Node runtime, which is why it is larger than a
# plain Python service. Installing it at runtime instead would mean every
# container restart waits on npm and fails without network egress.
#
# Node is copied from the official image rather than added via the NodeSource
# apt repository. Fetching that repo's signing key needs curl over TLS, which
# fails outright behind a corporate proxy that re-signs certificates -- and the
# error it produces ("unable to get local issuer certificate") looks nothing
# like the real cause. Copying binaries needs no network beyond the registry
# pull Docker is already doing.
#
# Both stages are pinned to bookworm on purpose: Node built against one glibc
# will not run on an image with another, and python:3.12-slim has since moved
# to trixie.
#
# Node 22, not 20. The undici bundled inside @salesforce/cli calls
# worker_threads.markAsUncloneable, which landed in Node 22.10 and was never
# backported to 20 -- so on Node 20 the CLI installs cleanly, reports its
# version cleanly, and then dies with "webidl.util.markAsUncloneable is not a
# function" the first time anything touches an org. Node 22 is Active LTS and
# is what the CLI is built against.
#
# psql is here because the entrypoint applies the schema and migrations itself
# rather than trusting Postgres's one-shot init directory, which only fires on
# an empty volume and skips the migrations entirely.

FROM node:22-bookworm-slim AS node
FROM python:3.12-slim-bookworm AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates postgresql-client tini \
    && rm -rf /var/lib/apt/lists/*

COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -sf /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -sf /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && node --version && npm --version

# The CLI phones home by default. This is pointed at a client's org, so opt out.
ENV SF_DISABLE_TELEMETRY=true

RUN npm install -g @salesforce/cli --omit=dev \
    && npm cache clean --force \
    && sf --version \
    # `sf --version` loads none of the org plumbing, so it stayed green while
    # the CLI was fatally broken on Node 20. Exercise the path that actually
    # failed -- jsforce, and through it undici -- so a bad Node/CLI pairing
    # breaks the build here instead of the first analysis run on the VM.
    # A dummy token is expected to be rejected; only a load-time crash matters.
    && out=$(SF_ACCESS_TOKEN=dummy sf org login access-token \
               --instance-url https://example.my.salesforce.com --no-prompt 2>&1 || true) \
    && case "$out" in \
         *"is not a function"*|*"Cannot find module"*|*"SyntaxError"*) \
           echo "sf CLI is broken on $(node --version):" >&2; echo "$out" >&2; exit 1 ;; \
       esac \
    && echo "sf CLI smoke test passed on $(node --version)"

# config.py derives REPO_ROOT as parents[2] of backend/app/config.py, so the
# code must live at /app/backend for /app/.env and /app/.cache to resolve the
# way they do in development. Moving it breaks path resolution silently.
WORKDIR /app/backend

# Dependencies first: this layer only rebuilds when pyproject changes, not on
# every source edit.
COPY backend/pyproject.toml ./
RUN python -m pip install --upgrade pip \
    && python -m pip install ".[anthropic]" \
    && python -m pip install "uvicorn[standard]"

COPY backend/ ./

# Non-root. The sf CLI writes state into $HOME, so it needs a real one.
RUN useradd --create-home --uid 10001 app \
    && mkdir -p /app/.cache /home/app/.sf /home/app/.cache \
    && chown -R app:app /app /home/app
USER app
ENV HOME=/home/app

COPY --chown=app:app deploy/entrypoint.sh /usr/local/bin/entrypoint.sh

EXPOSE 8000

# tini reaps the sf CLI subprocesses the retrieve stage spawns; without an init
# they accumulate as zombies over a long-lived container.
ENTRYPOINT ["/usr/bin/tini", "--", "/bin/sh", "/usr/local/bin/entrypoint.sh"]
