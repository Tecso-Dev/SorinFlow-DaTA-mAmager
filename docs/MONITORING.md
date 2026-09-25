# Monitoring — design and contract

Decided 2026-09-25:

- Monitoring lives in our own panel. There is no Prometheus or Grafana: the 8 GB node has about 6.2 GiB usable, and pod limits already add up to about 6.6 GiB.
- Alerts go to Telegram.
- An external uptime check runs in GitHub Actions every 5 minutes.

This file is the contract between the pieces. Change it together with the code that depends on it.

## Data flow

```
host: sorinflow-monitor.timer (root, every 60 s)
  └─ deploy/monitor/host_snapshot.py ──writes──> <data-pvc>/monitor/host.json
                                                  (/app/data/monitor/host.json in pods)
scheduler role: loop "monitor" (every 60 s)
  ├─ reads host.json, measures queue/Redis/Postgres/backups, polls GitHub every 5 min
  ├─ Postgres monitor_samples  (history, 8 days)
  ├─ Redis sf:monitor:*        (latest snapshot, CI cache, alert state and log)
  └─ Telegram                  (backup_service.send_text, same chats as the backups)
api role: /api/monitoring/{system,services,cicd,history,alerts}  (root, super_admin)
panel: «پایش سامانه» tabs
GitHub-hosted runner: .github/workflows/uptime.yml ── Telegram directly; its run
  conclusions are the panel's uptime record
```

No pod gets a Kubernetes API token and no NetworkPolicy changes. The host already runs kubectl as root, the way `scripts/dr_backup.sh` does, and hands the result over through the shared volume, the way `data/dr-status.json` does.

## 1. `monitor/host.json` (schema v1)

The host writes this file atomically every 60 s: it writes a temp file in the same directory, fsyncs it, then does `os.replace`. The directory is 0755 and the file is 0644. Each section fails on its own: a failed section gets `"ok": false, "error": "<short text>"` and the rest of the file is still written.

```json
{
  "v": 1,
  "at": "2026-09-25T08:00:00+00:00",
  "took_ms": 1830,
  "host": {
    "ok": true, "error": null,
    "hostname": "sorinflow-1", "kernel": "6.8.0-45-generic", "uptime_s": 123456,
    "cpus": 4, "cpu_pct": 12.5, "load": [0.52, 0.41, 0.30],
    "mem": {"total_mb": 7936, "available_mb": 3010, "swap_total_mb": 0, "swap_used_mb": 0},
    "disks": [
      {"name": "root", "mount": "/", "total_gb": 80.0, "used_gb": 30.1, "pct": 37.6, "inodes_pct": 5.0},
      {"name": "data", "mount": "/var/lib/rancher/k3s/storage/…_sorinflow_data-pvc", "total_gb": 80.0, "used_gb": 30.1, "pct": 37.6, "inodes_pct": 5.0}
    ],
    "net": {"iface": "eth0", "rx_bytes": 123, "tx_bytes": 456},
    "services": {"k3s": "active", "runner": "active", "dr_timer": "active", "ufw": "active"},
    "tls": {"host": "sorinflow.com", "not_after": "2026-12-01T00:00:00+00:00", "days_left": 67, "error": null},
    "reboot_required": false
  },
  "k8s": {
    "ok": true, "error": null,
    "nodes": [{"name": "sorinflow-1", "ready": true, "version": "v1.36.2+k3s1",
               "pressure": {"memory": false, "disk": false, "pid": false},
               "cpu_m": 850, "mem_mb": 4200, "alloc_cpu_m": 4000, "alloc_mem_mb": 7936}],
    "pods": [{"ns": "sorinflow", "name": "backend-6dfdc7d968-dq55n", "app": "backend",
              "phase": "Running", "ready": true, "restarts": 0,
              "last_reason": null, "last_finished_at": null, "started_at": "…",
              "cpu_m": 12, "mem_mb": 210, "mem_limit_mb": 1024}],
    "deployments": [{"ns": "sorinflow", "name": "backend", "desired": 2, "ready": 2,
                     "updated": 2, "available": 2, "image_tag": "219a85e"}],
    "jobs": [{"ns": "sorinflow", "name": "migrate-219a85e", "succeeded": 1, "failed": 0,
              "active": 0, "completed_at": "…"}],
    "events": [{"ns": "sorinflow", "reason": "BackOff", "object": "Pod/worker-…",
                "message": "…", "count": 3, "last_at": "…"}]
  }
}
```

- **Namespaces:** `sorinflow`, plus `sorinflow-staging` if it exists, plus `kube-system`.
- **`app`:** taken from the label `app`, then `app.kubernetes.io/name`, then `k8s-app`, then the name without its hash.
- **Pod fields:**
  - `restarts` is the sum over all containers.
  - `last_reason` is `lastState.terminated.reason` from the most recent termination, for example `OOMKilled`.
  - `mem_limit_mb` is null when there is no limit.
- **Metrics:** CPU and memory come from `metrics.k8s.io` through `kubectl get --raw`. If metrics-server is missing, those fields are null.
- **Events:** only `type=Warning` events from the last 60 minutes, at most 50, with `message` cut to 300 characters.
- **What never goes into the file:** no Secret, no env value and no pod spec beyond the fields above.

## 2. Redis keys

| key | what |
|---|---|
| `sf:monitor:latest` | JSON, the last combined snapshot the loop computed. TTL 10 min. |
| `sf:monitor:cicd` | JSON, the GitHub data. Refreshed every 5 min, TTL 30 min. |
| `sf:monitor:alerts` | hash: alert key → JSON `{level, title, detail, since, last_sent, count}` |
| `sf:monitor:alert_log` | list, LPUSH plus LTRIM 0 199, of JSON `{at, key, level, state: "firing"\|"resolved", title, detail, sent}` |
| `sf:monitor:prev` | hash of what the rules compare against: restart counts, the last OOM, run ids already alerted |

## 3. Postgres `monitor_samples`

Migration `0017` (down_revision `0016`) is additive only. It creates:

- `monitor_samples(id BIGSERIAL PRIMARY KEY, ts TIMESTAMPTZ NOT NULL, metric VARCHAR(64) NOT NULL, value DOUBLE PRECISION NOT NULL)`;
- the index `ix_monitor_samples_metric_ts (metric, ts)`.

The loop writes one row per metric every 60 s and deletes rows older than 8 days once an hour.

Metrics:

- `host.cpu_pct`, `host.mem_used_pct`, `host.load1`, `host.disk_root_pct`, `host.disk_data_pct`, `host.net_rx_bps`, `host.net_tx_bps`;
- `k8s.restarts_total`, `k8s.pods_not_ready`;
- `pod.<app>.mem_mb` and `pod.<app>.cpu_m`, summed per app, for backend, worker, scheduler, postgres and redis;
- `queue.pending`, `queue.running`;
- `redis.used_mb`;
- `pg.connections`, `pg.db_mb`.

## 4. API

Every endpoint requires root or super_admin, the same as `/api/monitoring/runtime`. Each answers 200 with JSON and never a 500 for missing data: a missing source becomes `null` plus an `error` string.

| endpoint | answer |
|---|---|
| `GET /api/monitoring/system` | `{at, age_s, stale, error, host, k8s}`. `stale` is true when the file is older than 180 s or missing. `host` and `k8s` are the sections of host.json. |
| `GET /api/monitoring/services` | `{at, runtime, queue: {pending, running, claims, oldest_pending_s}, redis: {used_mb, max_mb, evicted_keys, clients, uptime_s}, postgres: {db_mb, connections: {active, idle, total}, longest_query_s}, backups: {snapshot: {at, offsite_ok}, dr: {at, ok, alert}}}`. `runtime` has the same shape as `/runtime`. |
| `GET /api/monitoring/cicd` | `{repo, fetched_at, error, deployed_sha, main_sha, behind, workflows: [{name, file, last: {status, conclusion, branch, event, sha, started_at, duration_s, url}, recent: [conclusion, …up to 10]}], uptime: {pct_24h, pct_7d, last_down_at, runs: [{at, ok}, …up to 48]}}` |
| `GET /api/monitoring/history?metrics=a,b&hours=24` | `{hours, series: {metric: [[iso_ts, value], …]}}`. `hours` is one of 1, 6, 24, 72 or 168. Each series has at most 300 points (bucket averages). At most 12 metrics; unknown names are ignored. |
| `GET /api/monitoring/alerts` | `{active: [{key, level, title, detail, since}], log: [up to 100], telegram: {configured}}` |
| `POST /api/monitoring/alerts/test` | Sends «پیام آزمایشی پایش» and answers `{sent}`. At most one call per minute. |

## 5. Alert rules

Messages are in Persian: a title line, then the detail. A firing critical alert starts with «🔴» and a warning with «🟠». When it clears, «✅ برطرف شد: …» is sent. If Telegram is not configured, the alert is still logged with `sent: false`.

- **Grace:** for the first 3 minutes after the loop starts, which is where a deploy lands, no rule fires. The exception is `host_silent`, which may fire after 5 minutes.
- **Repeats:** a critical alert repeats every 6 hours while it lasts. A warning is sent once.

| key | fires when | level | for |
|---|---|---|---|
| `host_silent` | host.json is missing or older than 180 s | critical | – |
| `node:<name>` | the node is not Ready, or under memory, disk or PID pressure | critical | 2 min |
| `mem_low` | host available memory under 10% (warning), under 5% (critical) | warning/critical | 5 min |
| `disk:<name>` | disk at 85% or more (warning), 95% or more (critical) | warning/critical | – |
| `load_high` | load1 above 2 × cpus | warning | 10 min |
| `crash_loop:<app>` | the app's restarts went up by 3 or more within 15 min | critical | – |
| `oom:<app>` | a new OOMKilled termination | warning | once per termination |
| `deploy_degraded:<name>` | ready is below desired | critical | 5 min, or 10 min while a rollout is under way |
| `job_failed:<name>` | the job has `failed > 0` | critical | once per job |
| `heartbeat:<process>` | a process heartbeat is older than 300 s, or a loop is stale | warning | – |
| `queue_stuck` | pending is above 0, no claim exists, and the oldest pending job has waited over 30 min | warning | – |
| `ci_failed:<file>` | the last run on `main` concluded `failure` (deploy.yml is critical; ci, e2e and k8s are warnings). The work branch never alerts. | critical/warning | once per run id |
| `backup:dr` | the last DR run is over 26 h old, or `last_alert` is set | critical | – |
| `backup:snapshot` | the last offsite snapshot is over 26 h old | warning | – |
| `tls` | the certificate has under 14 days left (warning) or under 3 days (critical) | warning/critical | – |
| `host_service:<name>` | k3s, the runner or the DR timer is not active (warning); ufw is not active (critical) | warning/critical | 2 min |

## 6. CI/CD collection

- **Settings:**
  - `github_repo` is read from env `GITHUB_REPOSITORY`. Its default is this repository's slug.
  - `github_token` is optional and read from env only. The repository is public, so the anonymous rate limit is enough; ETags keep the polling well inside it.
- **Every 5 minutes, three calls:**
  - `GET /repos/{repo}/actions/runs?branch=main&per_page=50`
  - `GET /repos/{repo}/actions/runs?branch=sorinflow-v2&per_page=30`
  - `GET /repos/{repo}/commits/main`

  Each call sends `If-None-Match` with the stored ETag, uses a 10 s timeout, goes out the normal outbound way, and never raises: a failure lands in `cicd.error`.
- **Workflows shown:** `ci.yml`, `e2e.yml`, `k8s.yml`, `deploy.yml`, `uptime.yml` and `staging.yml`.
- **Deploy lag:** `deployed_sha` is the first 7 characters of `settings.git_sha`. `behind` is true when `main_sha` differs from it.
- **Uptime:** taken from the `uptime.yml` runs. `success` counts as up and `failure` as down; `cancelled` and `skipped` are ignored.

## 7. External uptime check — `.github/workflows/uptime.yml`

- **Triggers:** `schedule: '*/5 * * * *'` and `workflow_dispatch`.
- **Runner:** `runs-on: ubuntu-latest`, **never** the self-hosted runner.
- **Job settings:** `permissions: {actions: read, contents: read}`, `timeout-minutes: 5`, `concurrency: uptime`.
- **Logic:** lives in `scripts/uptime_check.sh`.
  - Target: `UPTIME_URL`, defaulting to `https://sorinflow.com` through `vars.UPTIME_URL`.
  - Checks: `/health` must report `"status":"healthy"` and `/ready` must answer. The site counts as down only after 3 attempts, 20 s apart, all fail.
  - Previous state: the conclusion of the previous completed run, from `gh run list --workflow uptime.yml --branch main --status completed --limit 1 --json conclusion`.
- **Telegram messages** are sent when the site goes down, when it comes back up, and on every 12th consecutive down run (about hourly). They need two secrets, `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_IDS` (comma-separated). If either is missing, the job writes a `::warning::` and carries on.
- **Result:** the job fails when the site is down, so the run conclusion is the uptime record.
- **Local testing:** `DRY_RUN=1` prints the message instead of sending it.

## 8. Panel

«پایش سامانه» gets tabs:

- نمای کلی — the current content, unchanged;
- سرور;
- کوبرنتیز;
- سرویس‌ها;
- CI/CD;
- هشدارها — with the «پیام آزمایشی» button.

Only root and super_admin see the new tabs. The page is dark and RTL and uses only the site's own tokens. It never uses `prompt`, `confirm` or `alert`. History charts use `chart.min.js` with a 1h / 6h / 24h / 7d selector. The page refreshes every 30 s, but only while the section is visible and the browser tab is focused.
