# 06: Phoenix in Docker Compose, and services bound to localhost

**What to build:** `docker compose up -d` starts Phoenix next to Postgres and Neo4j: image `arizephoenix/phoenix:20.19.0`, the UI and OTLP HTTP on 6006 and gRPC on 4317, a named volume for its working directory so traces survive `docker compose down`, and `PHOENIX_TELEMETRY_ENABLED=false` and `PHOENIX_ALLOW_EXTERNAL_RESOURCES=false`. Phoenix, Postgres and Neo4j publish their ports on `127.0.0.1` only. `.env.example` lists `PHOENIX_COLLECTOR_ENDPOINT` and the text-capture setting, commented out. See docs/research/phoenix-tracing.md section 1.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] The Phoenix image tag is pinned exactly, like the other services.
- [ ] Every published port in the compose file is bound to `127.0.0.1`.
- [ ] The Phoenix healthcheck is confirmed working in the distroless image, or left out with a comment saying why.
- [ ] After `docker compose up -d`, the Phoenix UI loads at `http://localhost:6006`, and a `docker compose down` then `up -d` keeps an existing trace (send one test span by hand to check).
- [ ] The UI makes no requests to third-party hosts (checked in the browser's network log).
- [ ] Existing database tests pass against the rebound Postgres: `uv run --env-file .env pytest`.
- [ ] CLAUDE.md Commands mention that Phoenix runs with `docker compose up -d` and where its UI is.
