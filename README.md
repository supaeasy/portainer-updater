**English | [Deutsch](README.de.md)**

# Portainer Updater

Replaces manually clicking through every Portainer stack: detects available
image updates, has Claude check the release notes between the current and
new version for breaking changes and required `docker-compose.yml` changes
(e.g. pinned sub-versions, as regularly happens with immich), and shows
everything in one overview with checkboxes. Selected stacks are updated and
redeployed via the Portainer API.

## Architecture

```
┌──────┐   Update detected   ┌──────────────────┐   Releases    ┌────────┐
│ WUD  │ ──────────────────▶ │  analysis-layer   │ ────────────▶ │ GitHub │
│      │  (http trigger)     │  (FastAPI)        │               └────────┘
└──────┘                     │                   │   compose.yml  ┌───────────┐
   ▲                         │                   │ ─────────────▶ │ Portainer │
   │ read-only                │                   │ ◀───────────── │    API    │
   │ docker.sock               │  Claude analysis  │  redeploy       └───────────┘
   └─────────────────────────  + SQLite storage    │
                               │  + dashboard UI    │
                               └──────────────────┘
                                        ▲
                                        │ Browser (checkboxes, "update")
```

- **WUD** (`getwud/wud`) watches all running containers via a (read-only)
  Docker socket and reports new image versions. WUD itself never updates
  anything - that's deliberate, nobody should auto-apply an update without
  seeing the analysis first.
- **analysis-layer** is the actual piece built in this repo: receives WUD
  notifications, fetches the GitHub release notes between the old and new
  version, fetches the affected stack's current compose file straight from
  Portainer, and has Claude produce an assessment (risk level, plain-language
  summary, required compose changes including a suggested patch). Results are
  stored in SQLite and shown on the dashboard.
- **Dashboard** (served at `/` by the analysis-layer): list of all open
  updates with risk assessment, checkboxes, and an "Update selected" button.

### Why not one webhook per stack?

Portainer ships ready-made stack webhooks (Business Edition adds `tag=` /
`pullimage=` query parameters to force a specific tag on trigger). The
analysis-layer uses the Portainer REST API directly instead
(`GET/PUT /api/stacks/{id}`, which also works on the Community Edition - so
the choice isn't about which edition you run): a webhook can only ever
replace a single tag that was already parameterized as a variable beforehand.
For stacks with several components pinned to literal versions in the compose
file (immich: app image, ML image, and Postgres/vectors image each pinned
separately), that's not enough - the compose file itself needs to be edited
in multiple places, and only the API can do that. It reads the compose file,
applies Claude's suggested patch if needed, and redeploys the stack with
`RepullImageAndRedeploy`.

**Bonus with Business Edition:** under *Host -> Setup* (Docker Standalone) or
*Environment -> Setup* you can enable "Show an image(s) up to date indicator
for Stacks, Services and Containers" - a simple green/orange checkmark right
in the Portainer UI (digest comparison, no version/breaking-change context).
A nice complementary signal, but it doesn't replace the dashboard here.

## Setup

### 1. Create a Portainer API key

Portainer UI -> User settings -> Access tokens -> Add access token.

**Recommendation:** create a dedicated, restricted Portainer user for this,
scoped via RBAC to only the environments this tool should manage - not the
admin account. Otherwise the key can modify *any* stack in Portainer.

### 2. Configure the repo

```bash
cp .env.example .env
cp stacks.yml.example stacks.yml
```

Fill in `.env`: `PORTAINER_URL`, `PORTAINER_API_KEY`, `ANTHROPIC_API_KEY`.
Optionally `GITHUB_TOKEN` (without a token, GitHub's public rate limit of
60 requests/hour applies, which can get tight with many stacks).

Fill in `stacks.yml`: for every container you want watched, enter the exact
Docker container name, the Portainer stack name, the Portainer environment
ID, and the GitHub repo (`owner/repo`) used for the changelog analysis.
Containers without an entry show up on the dashboard flagged as "not
configured in stacks.yml", but are neither analyzed nor updated
automatically.

### 3. Start it

```bash
docker compose up -d
```

Dashboard: `http://<host>:8000` (port configurable via `DASHBOARD_PORT` in
`.env`). WUD's own UI (optional, for cross-checking): `http://<host>:3939`.

## Day-to-day flow

1. WUD checks all containers every 6 hours (configurable via
   `WUD_WATCHER_CRON`). When it detects an update, it calls the
   analysis-layer, which automatically kicks off the analysis.
2. The analysis-layer additionally polls WUD itself every hour (configurable
   via `ANALYSIS_POLL_INTERVAL_MINUTES`) as a safety net, in case the webhook
   doesn't arrive or an update was already available before the dashboard's
   first start.
3. The dashboard shows one row per open update with a risk badge (none /
   minor changes / major changes / breaking changes), a plain-language
   summary, and - where relevant - the suggested compose diff.
4. Check the stacks you want, optionally enable "Apply suggested compose
   change", and click "Update selected". The analysis-layer writes the
   (possibly patched) compose file back to Portainer and redeploys the stack
   with freshly pulled images.
5. "Dismiss" marks an update as handled without changing anything (e.g. if
   you already did it manually).

## Known limitations / please verify

- The Portainer API field names (`StackFileContent`, `RepullImageAndRedeploy`,
  the `X-API-Key` header) were verified directly against the source code of
  tag `2.39.5` (matching the Business Edition LTS in use here) - so they
  should work without changes. If you later upgrade to a newer Portainer
  version, double-check against `PORTAINER_URL/api/docs` (Swagger) on your
  own instance if in doubt.
- Digest-only updates (no version tag, e.g. `:latest`) can't be compared via
  GitHub releases - the dashboard flags these as "update detected, no
  analysis possible".
- The `compose_patch` Claude suggests is just that - a suggestion, not a
  guaranteed-correct patch. Review the diff on the dashboard before enabling
  "apply change", especially for stacks holding sensitive data (databases).
- The Anthropic API key incurs ongoing cost (one analysis per newly detected
  version combination, not per poll - results are cached).
