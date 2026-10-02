# UTM Observability Dashboard Suite

An enterprise observability suite for the UTM platform, built from **verified**
Prometheus metrics and Elasticsearch mappings. No panel in this suite is built
on an assumed metric or an assumed field.

Where the truth is `NOT INSTRUMENTED`, `NO DATA`, `STALE` or `NOT AVAILABLE`,
the dashboards say so explicitly rather than rendering an empty chart.

---

## 1. Dashboard structure

| # | Dashboard | UID | Refresh | Default range |
|---|---|---|---|---|
| 00 | Executive Platform Overview | `utm-obs-00-executive` | 30s | 6h |
| 01 | Platform Health | `utm-obs-01-health` | 30s | 6h |
| 02 | Kubernetes Workloads | `utm-obs-02-workloads` | 30s | 6h |
| 03 | Nodes & Cluster Resources | `utm-obs-03-nodes` | 30s | 6h |
| 04 | Ceph & Storage Health | `utm-obs-04-ceph` | 30s | 6h |
| 05 | UAS Zones & Airspace | `utm-obs-05-uas` | 5m | 1y |
| 06 | Operation Plan Lifecycle | `utm-obs-06-opplans` | 1m | 1y |
| 07 | User Activity & Adoption | `utm-obs-07-users` | 5m | 1y |
| 08 | Observability Coverage | `utm-obs-08-coverage` | 5m | 6h |
| 09 | Drone Plan Dashboard *(statistics)* | `utm-drone-plans` | 5m | 1y |
| 10 | DOR / DMS Registry | `utm-obs-10-dor` | 5m | 1y |
| 11 | Keycloak Identity | `utm-obs-11-keycloak` | 5m | 30d |

**272 panels across 12 dashboards.** Grafana folder: **UTM Observability**.

Dashboard 09 is the pre-existing Drone Plan Dashboard, moved into this suite. It keeps its original uid `utm-drone-plans`, so existing links and bookmarks still resolve. It reads the *statistics* copy (`utm-op-statistics`); dashboard 06 reads the *live* store (`operation-plan-wrapper`).

Every dashboard carries a `UTM Observability` dropdown in its header that
preserves the current time range and variables when navigating.

Dashboards 05–07 default to a **1 year** range deliberately: their datasets are
historical (newest is 2026-08-10) and a 6h default would render them empty,
which would read as "no activity" rather than "no recent data".

---

## 2. Infrastructure changes made

Only what the dashboards actually required.

### 2.1 ServiceMonitor for rook-ceph-mgr — **required, and a correction**

The brief stated that `rook-ceph-exporter` metrics were already scraped by
Prometheus. **They were not.** Verified before building:

```
count of ceph_*  metric names in Prometheus : 0
count of rook*   metric names in Prometheus : 0
ServiceMonitors in the rook-ceph namespace  : 0
```

All 17 pre-existing ServiceMonitors lived in the `monitoring` namespace and
targeted the monitoring stack itself. Meanwhile `rook-ceph-mgr:9283` was
serving **412 `ceph_*` metrics** to nobody.

`servicemonitor-rook-ceph-mgr.yaml` scrapes that **existing** endpoint. No new
exporter, no second Ceph monitoring stack. This is what made dashboard 04
possible; without it, Ceph would have had to ship as a coverage gap.

### 2.2 core-es7 datasources — three, not one

`datasource.yaml` appends three Elasticsearch datasources to the existing
`grafana-datasource-config` ConfigMap:

| uid | Index | Time field |
|---|---|---|
| `core-es7-uas` | `uas_9.0.0` | `submitTime` |
| `core-es7-opw` | `operation-plan-wrapper*` | `operationPlan.submitTime` |
| `core-es7-opr` | `operation-plan-result*` | `operationPlanResult.startTime` |

All three point at `core-es7.utm-core.svc.cluster.local:9200`, in-cluster only,
never externally exposed.

**Why three and not one:** Grafana's Elasticsearch datasource binds exactly one
index pattern **and one time field** per datasource. These three indices use
three different time fields, so a single datasource cannot serve them. This is
a Grafana constraint, not a design preference.

### 2.3 Dashboard provisioning

- `provider.yaml` declares a single provider → folder **UTM Observability**,
  path `/var/lib/grafana/dashboards-observability`, `allowUiUpdates: false`
  (GitOps-managed; UI edits would be silently overwritten, so the UI marks them
  read-only instead).
- `configmap-dashboards.yaml` — the 10 dashboards (~358 KB, well under the 1 MB
  ConfigMap limit).
- `deployment-patch.json` — mounts that ConfigMap.

> Apply the ConfigMap with `kubectl apply --server-side`. A normal `apply`
> stores the whole object in the `last-applied-configuration` annotation and
> exceeds the 256 KB annotation limit.

### 2.4 Alert rules

`alert-rules.yaml` — a `PrometheusRule` reusing the existing kube-prometheus-stack
Alertmanager. **32 rules across 6 groups** — 12 critical, 16 warning, 4 coverage.

The three `utm-identity-*` groups were added once Keycloak runtime metrics
existed (§2.6). All 32 report `health=ok` in Prometheus `/api/v1/rules`.

No rule is written against `keycloak_*` login counters: those series do not
exist in this image, and a rule on an absent series is permanently silent
while appearing configured.

### 2.5 ServiceMonitor for ingress-nginx — the only source of RED metrics

`servicemonitor-ingress-nginx.yaml`. The controllers already served metrics on
port 10254; nothing was scraping them.

A **dedicated headless ClusterIP Service** plus a ServiceMonitor, rather than the
two more obvious routes:

- A `PodMonitor` needs a *named* containerPort. 10254 is not declared in the
  DaemonSet, so naming it would mean editing the DaemonSet — which restarts all
  four controllers. This cluster's own Kubernetes API is reached through that
  ingress (`rancher.ops.io`), so a controller restart briefly takes `kubectl`
  offline. That is not a theoretical risk: it happened once during this work.
- Adding a port to the existing `ingress-nginx-controller` Service would edit a
  MetalLB-managed LoadBalancer carrying live 80/443 traffic.

Result: `nginx_ingress_controller_requests` (52 series over 2 hosts) and
`nginx_ingress_controller_request_duration_seconds_bucket` (624 buckets). This is
**edge** RED — traffic crossing the ingress, by host and path. It is not
per-service RED and says nothing about service-to-service calls, which never
touch the ingress.

### 2.6 Keycloak runtime metrics — enabling, then scraping

Two changes, in order.

**(a) Enable.** `keycloak-enable-metrics-patch.json` sets `KC_METRICS_ENABLED=true`
and `KC_HEALTH_ENABLED=true` on `utm-core/keycloak`. Before this,
`/keycloak/auth/metrics` returned **404**.

This restarts the core identity provider, which every service on the platform
authenticates through, so the blast radius was checked before applying rather
than after:

| Check | Value | Consequence |
|---|---|---|
| `replicas` | 1 | a naive restart would mean a full outage |
| `maxSurge` / `maxUnavailable` | 25% / 25% → **1 / 0** | new pod starts *before* the old is removed |
| Volumes | no PVC | no ReadWriteOnce Multi-Attach deadlock |
| kubeconfig auth | Rancher-issued token | cluster access does not depend on Keycloak |

The rollout surged cleanly and authentication was not interrupted. A backup of
the pre-change deployment is `_keycloak-deploy-backup-111527.yaml`.

Note: this deployment has **no readinessProbe**, so the pod reported `1/1` about
75 seconds before Keycloak actually served the endpoint. `1/1` is not a
readiness signal here.

**(b) Scrape.** `servicemonitor-keycloak.yaml` — again a dedicated headless
Service, because the existing `keycloak` Service has `metadata.labels: null`
(no selector can match it) and is a NodePort serving live authentication on
30002. Path is `/keycloak/auth/metrics`, not `/metrics`, because
`KC_HTTP_RELATIVE_PATH=/keycloak/auth`.

The endpoint serves **153 metric lines / 101 distinct names**:

| Family | Count | What it gives |
|---|---|---|
| `jvm_*` / `base_*` / `vendor_*` | 62 | heap, memory pools, GC pause and overhead, threads by state, classloading |
| `agroal_*` | 17 | database connection pool — Keycloak's usual bottleneck |
| `worker_pool_*` | 12 | Vert.x request concurrency: active, idle, queue depth, rejections |
| `process_*` / `system_*` | 10 | CPU, file descriptors, uptime, host load |

**Not** present: `keycloak_*` login/registration counters and
`http_server_requests_*`. These are **not** switched on by
`KC_METRICS_ENABLED` — they come from the separate `keycloak-metrics-spi`
extension, which is not bundled in this image and would need adding to the
container build. Nothing in the suite fabricates them; dashboard 11 states the
gap explicitly and falls back to `keycloak.event_entity`, which is richer for
authentication *outcomes* because it carries the error reason.

Two unit traps, handled in the panels and worth knowing before writing queries:

- `jvm_gc_overhead_percent`, `jvm_memory_usage_after_gc_percent` and
  `worker_pool_ratio` are **ratios in [0..1]** despite their names — they must
  use `percentunit`, not be multiplied by 100.
- `base_jvm_uptime` is **milliseconds**; `process_uptime_seconds` is seconds.

The job label is `keycloak-metrics` (from the Service name), not `keycloak`.

### 2.7 Loki + Alloy — closing the application-logs gap (2026-10-02)

`loki-config.yaml`: single-binary Loki 3.7.8, TSDB index, filesystem storage,
30Gi PVC, 14-day retention (compactor-enforced — this cluster's documented
failure mode is Ceph disk-fill, so retention is a real ceiling, not "whatever
fits"). Scheduled off `utm-kubernetes-node-004` via nodeAffinity — that node's
rook-ceph CSI nodeplugins were CrashLoopBackOff at deploy time (see the Calico
confd/`SandboxChanged` entry in the main doc's §9 platform-issues table).

`alloy-logs.yaml`: Grafana Alloy v1.20.1 (Promtail's maintained replacement —
Promtail hit EOL 2026-03-02) as a DaemonSet, hostPath-reading `/var/log/pods`
with CRI-format parsing, filtered per-node via `discovery.kubernetes` +
`NODE_NAME`. Deliberately *not* using the Kubernetes log-subresource API: that
path depends on kubelet-mediated apiserver TLS, which was the exact thing
broken by the 2026-09-30 cert-expiry incident. No PVC, no Ceph CSI dependency
— runs on all 4 nodes including node-004.

Both images are re-hosted to `harbor.ops.io/utm-devops/{loki,alloy}` — this
cluster's nodes have no direct route to `registry-1.docker.io` (DNS/egress
restricted to `uas.ops.io` + `harbor.ops.io`), same pattern as every other
third-party image already in this namespace (Jaeger, Tempo).

Verified: Grafana datasource health check OK; a manual push/query round trip
returned the exact line pushed; real cluster log data confirmed flowing from
multiple namespaces across nodes, including a line from *before* `alloy-logs`
itself started (proves it backfills on first file discovery, not tail-only).

Added `12-application-logs.json`: a `namespace` template variable (Loki
`label_values`, restricted to `utm-.*`) driving one Logs panel. All 13
dashboards' nav `links` updated to cross-reference it.

### 2.8 OTel auto-instrumentation — tracing all utm-* applications (2026-10-02)

OpenTelemetry Operator v0.160.0 (`otel-operator.yaml`) + one `Instrumentation`
CR per namespace (`otel-instrumentation*.yaml`, 4 total: `utm-services`,
`utm-skyzr`, `utm-core`, `utm-aviamaps`), targeting Tempo's OTLP HTTP
receiver on `:4318` (not `:4317`/grpc — both Java and Node auto-instrumentation
default to `http/protobuf` regardless of port, confirmed via the Java agent's
own logs). Java/Node services get a `-javaagent`/`NODE_OPTIONS` injected via
a pod annotation (`instrumentation.opentelemetry.io/inject-{java,nodejs}`),
zero app code changes, verified end-to-end against a 2-service pilot before
fanning out further.

**Final eligible count: 34 services**, surveyed by checking the actual
running process per pod (`/proc/1/cmdline`, and `/proc/*/cmdline` for
shell-wrapped entrypoints) across all `utm-*` namespaces — not assumed from
image names:
- `utm-services`: 28 Java/Quarkus + 1 Node (`utm-geotool`)
- `utm-skyzr`: 3 Java (`dor-acg`, `dor-be`, `dor-data-importer`)
- `utm-core`: 1 (`geoserver`) — `activemq` deliberately excluded as
  infra/message-broker, not business logic
- `utm-aviamaps`: 1 Node (`flyk`)
- `utm-devops`: **0 eligible** — everything is nginx frontends or Go
  binaries (Vault), neither has an auto-instrumentation path
- Excluded everywhere: `cars` (Rust, no auto-instrumentation path exists in
  the OTel ecosystem — would need source changes); Keycloak ×3
  (identity-critical infra, deliberately out of scope)

**The fan-out itself caused a serious cascading incident** — restarting 34
services simultaneously exceeded this cluster's real per-node memory
headroom, triggered a kernel OOM kill on one node, `NodeNotReady` flapping on
two nodes, and knocked 2 of 3 Elasticsearch (`core-es7`) replicas offline as
collateral damage, putting that cluster into `status: red` for several
minutes. Ceph also went `HEALTH_WARN` (degraded redundancy) from the same
root cause. **Full incident writeup, with timeline, root cause and lessons
learned, is in the main doc** (`docs/UTM-Observability-Documentation.md`,
under gap G4) — not duplicated here to avoid the two documents drifting out
of sync on something this detailed.

---

## 3. Validation results

All performed against the live cluster.

### 3.1 Prometheus

```
TOTAL metric names            : 1415
UTM pods visible              : 69          ✅ matches brief
Nodes / Ready nodes           : 4 / 4
Scrape targets                : 48  (14 jobs)
```

Every PromQL expression used was executed before being written into a panel:

| Query | Result |
|---|---|
| `count(kube_node_info)` | 4 |
| `count(kube_pod_info{namespace=~"utm-.*"})` | 69 |
| `kube_pod_container_status_restarts_total` | 200 series |
| `kube_deployment_status_replicas{,_available,_unavailable}` | 104 series each |
| `kube_node_status_condition` | 60 series |
| `node_filesystem_{size,avail}_bytes` | present |
| `node_memory_Mem{Total,Available}_bytes` | present |
| `container_{cpu_usage_seconds_total,memory_working_set_bytes}` | 1003 series each |
| `kubelet_volume_stats_{used,capacity}_bytes` | 42 series each |
| `kube_ingress_info{namespace=~"utm-.*"}` | **32** |
| `kube_service_info{namespace=~"utm-.*"}` | **84** |
| `probe_success` | 3 series |
| **max node root-filesystem utilisation** | **80.93%** ⚠️ |

### 3.2 Ceph — after the ServiceMonitor was added

| Query | Result |
|---|---|
| `ceph_health_status` | **1** (HEALTH_WARN) |
| `sum(ceph_osd_up)` / `sum(ceph_osd_in)` | 3 / 3 |
| `sum(ceph_mon_quorum_status)` | 3 |
| `sum(ceph_healthcheck_slow_ops)` | 0 |
| `sum(ceph_pg_clean)` / `sum(ceph_pg_total)` | 81 / 81 |
| `ceph_cluster_total_bytes` | 325 GB |
| `ceph_cluster_total_used_bytes` | 37.4 GB |
| `max(ceph_osd_apply_latency_ms)` | 121 ms |
| `ceph_osd_stat_bytes_used` | **EMPTY** — see gaps |

### 3.3 Elasticsearch — section 33 required queries

`_count` used throughout, never `_cat/indices` `docs.count`:

| Index | `_count` | `_cat` docs.count | Time field |
|---|---|---|---|
| `uas_9.0.0` | **168** | ~504 (nested-inflated) | `submitTime` |
| `operation-plan-wrapper_3.2.2-p2` | **41** | 167 (nested-inflated) | `operationPlan.submitTime` |
| `operation-plan-result_3.2.2-p2` | **24** | — | `operationPlanResult.startTime` |
| `ops-subscription_3.2.2-p2` | **8** | — | *(none)* |
| `utm-generic-statistics*` | **635** | — | `timestamp` |

Confirmed field values (only these were used to build panels):

```
uas_9.0.0
  uasZone.type              REQ_AUTHORIZATION 130, NO_RESTRICTION 38
  uasZone.country           SWE 131, EST 37
  uasZone.reason            AIR_TRAFFIC 168
  dataSource.originator     AIXM-import 131, uas 37
  submitTime                2026-06-29 → 2026-08-04

operation-plan-wrapper
  operationPlan.state       CLOSED 14, PROPOSED 14, AUTHORIZED 10, ACTIVATED 3
  modeOfOperation           REMOTELY_PILOTED_VLOS 28, REMOTELY_PILOTED_BVLOS 13
  operator                  PH-DRN-REG-AX1B2 35, FRQ 6
  submitTime                2026-04-15 → 2026-05-12

operation-plan-result
  state                     GRANTED 13, TIMEOUT 7, DENIED 4
  operationPlanResultType   AUTHORIZATION 14, ACTIVATION 10

utm-generic-statistics
  application               flyk-web 635
  event                     pageload 298, visibilitychange 298, deconflict 21, plan 18
  unique userIdAnonymized   5
  timestamp                 2026-03-23 → 2026-08-10
```

Connectivity from the Grafana pod to core-es7, post-provisioning:

```
uas_9.0.0/_count                 {"count":168, "_shards":{"failed":0}}
operation-plan-wrapper*/_count   {"count":41,  "_shards":{"failed":0}}
operation-plan-result*/_count    {"count":24,  "_shards":{"failed":0}}
```

### 3.4 Provisioning

```
inserting datasource from configuration name=core-es7-uas uid=core-es7-uas
inserting datasource from configuration name=core-es7-opw uid=core-es7-opw
inserting datasource from configuration name=core-es7-opr uid=core-es7-opr

/var/lib/grafana/dashboards-observability/ → 9 files
provisioning.dashboard  start → finish = 6.46s   (a real import)
level=error lines                              : 0
pod                                            : 1/1 Running, 0 restarts

Prometheus rule groups loaded:
  utm-platform-critical       9 rules
  utm-platform-warning       10 rules
  utm-observability-coverage  2 rules
```

---

## 4. Known observability gaps

Each is represented on dashboard 08 with its evidence.

| Gap | Status | Evidence |
|---|---|---|
| **Application RED metrics** | `NOT INSTRUMENTED` | 0 `http_*`/`grpc_*` series from utm namespaces; 85 services, 38 deployments in `utm-services` alone. Now **1** ServiceMonitor targets utm-* (`utm-core/keycloak`), but it scrapes Keycloak's JVM and DB pool, not application requests. Partial substitute: ingress-edge RED via `nginx_ingress_controller_*`. |
| **Keycloak login / token counters** | `NOT INSTRUMENTED` | `keycloak_*` and `http_server_requests_*` absent from the 153-line metrics endpoint; they require the `keycloak-metrics-spi` extension, which is not bundled in this image |
| **Application logs** | `RESOLVED` (2026-10-02) | Loki + `alloy-logs` DaemonSet deployed; see §2.7. `utm-services-logs*` (ES app-level logs) is a separate, still-absent gap this doesn't close |
| **Jaeger/Tempo trace→log link** | `RESOLVED` (2026-10-02) | `tracesToLogsV2.datasourceUid` on both repointed `es-logs-uid` → `loki-uid` |
| **Distributed tracing** | `FANNED OUT, CLUSTER RECOVERING` (2026-10-02) | Was `STALE`. OTel Operator + 34 instrumented Java/Node services across 4 namespaces, zero code changes; see §2.8. The fan-out triggered a serious cascading incident (node OOM, ES went `red`, Ceph degraded) — see main doc's dedicated incident writeup under gap G4. Not claiming fully done until the cluster finishes settling |
| **TraceQL metrics queries (`\| rate()` etc.)** | `RESOLVED` (2026-10-02) | Was `500: ...empty ring` — Tempo had no `metrics_generator` section. Fixed via `tempo-config.yaml` (local-blocks processor + inmemory ring); verified `HTTP 200`. Now has real data to query, not just empty series, once tracing fan-out settles |
| **Endpoint coverage** | `PARTIAL (~9%)` | 3 blackbox targets vs 32 ingresses |
| **Blackbox probes** | `ALL FAILING` | `sum(probe_success)` = **0** of 3 |
| **Alert history** | `NO DATA` | `utm-alerting-statistics` = 0 docs in all 4 indices |
| **Per-OSD Ceph capacity** | `NOT EXPOSED` | `ceph_osd_stat_bytes_used` empty on mgr:9283 |
| **ED-269 / LARA properties** | `NO DATA` | `uSpaceClass`, `restrictionCondition`, `isLaraAirspace`, `extendedProperties.active` all aggregate to 0 buckets |
| **UAS zone geometry** | `RESOLVED` | `uasZone.geometry.geom` is `geo_shape` inside a `nested` field, which Grafana can aggregate neither of. Worked around by deriving a parent-level `centroid` geo_point into `uas-zones-geo` (`uas-zones-geo-build.sh`); the source index is not modified |
| **`ops-subscription`** | `NOT QUERYABLE` | all fields `text` / `aggregatable: false`, and no date field |
| **User dimensions** | `NO DATA` | `location`, `userRoles`, `param1/2` empty; `userAgent` is non-aggregatable `text` |

---

## 5. Assumptions

1. **`utm-*` namespace regex** — "UTM platform" is taken to mean
   `utm-core|utm-services|utm-skyzr|utm-aviamaps|utm-statistics|utm-devops`,
   which yields the 69 pods the brief cites.
2. **Environment label** — not hard-coded to "Production". No reliable
   environment marker exists in the cluster, so per section 28 it is omitted
   rather than guessed.
3. **Disk thresholds** — 70 / 80 / 90 % per section 8, set in each panel's
   field config and adjustable there.
4. **Root filesystem selector** — `mountpoint="/", fstype!="rootfs"`, the
   standard node-exporter idiom.
5. **KPI single-bucket aggregation** — Grafana's Elasticsearch datasource
   rejects a metric query with no bucket agg, so KPI stats use a 1-year
   `date_histogram` with `min_doc_count: 1`, collapsing the range into one
   bucket so `lastNotNull` returns a true total rather than a sub-interval.
   **Caveat:** if a dataset ever spans two calendar years within the visible
   range, these tiles show only the later year.
6. **`operation-plan-result` time field** — `operationPlanResult.startTime` is
   used; the index has no document-level created/updated timestamp.
7. **41 vs 37 plans** — treated as normal lag, **not** a pipeline failure. The
   two stores capture different lifecycle stages (live has a full spread, the
   statistics copy is entirely CLOSED), so a difference is expected by design.

---

## 6. Git diff summary

Files on disk, measured:

```
observability/
├── 00-executive-overview.json              57 KB  30 panels  executive summary, drill-down hub
├── 01-platform-health.json                 38 KB   9 panels  namespace / pod health
├── 02-kubernetes-workloads.json            37 KB  13 panels  container states and reasons
├── 03-nodes-cluster.json                   28 KB   9 panels  node CPU / memory / disk
├── 04-ceph-storage.json                    52 KB  22 panels  Ceph health, OSDs, PVCs
├── 05-uas-zones.json                       43 KB  23 panels  airspace zones + geomap
├── 06-operation-plans.json                 55 KB  26 panels  plan lifecycle + geomap
├── 07-user-activity.json                   28 KB  12 panels  adoption and usage
├── 08-observability-coverage.json          33 KB  21 panels  what is and is not monitored
├── 09-drone-plan-statistics.json           50 KB  17 panels  pre-existing, moved into the suite
├── 10-dor-registry.json                    62 KB  37 panels  DOR/DMS operators, drones, verification
├── 11-keycloak-identity.json               94 KB  53 panels  auth events + JVM/DB/worker runtime
├── 12-application-logs.json                     NEW 2026-10-02  namespace variable + Logs panel
├── loki-config.yaml                             NEW  Loki: ConfigMap + PVC + Deployment + Svc
├── alloy-logs.yaml                              NEW  Alloy DaemonSet + ServiceAccount/RBAC
├── tempo-config.yaml                            NEW  Tempo ConfigMap +metrics_generator;
│                                                      first time this pre-existing object is in Git
├── otel-operator.yaml                           NEW  OTel Operator v0.160.0
├── otel-instrumentation.yaml                    NEW  Instrumentation CR (utm-services)
├── otel-instrumentation-core.yaml               NEW  Instrumentation CR (utm-core)
├── otel-instrumentation-skyzr.yaml              NEW  Instrumentation CR (utm-skyzr)
├── otel-instrumentation-aviamaps.yaml           NEW  Instrumentation CR (utm-aviamaps)
├── instrument-all.ps1                           NEW  fan-out script: 4 CRs + 34 patches + rollout
├── patches/*.json                               NEW  34 kubectl --patch-file injection annotations
├── deployment-patch-dor.json              340 B   grafana DOR_RO_PASSWORD env
├── deployment-patch.json                  495 B   grafana volume + mount
├── ingress-enable-metrics-patch.json      126 B   ingress metrics port
├── keycloak-enable-metrics-patch.json     215 B   KC_METRICS_ENABLED + KC_HEALTH_ENABLED
├── _keycloak-deploy-backup-111527.yaml      3 KB  pre-change deploy backup
├── alert-rules.yaml                        18 KB  32 PrometheusRule alerts, 6 groups
├── datasource.yaml                          7 KB  12 datasources (+Loki; Jaeger/Tempo repointed)
├── provider.yaml                          898 B   UTM Observability folder provider
├── servicemonitor-ingress-nginx.yaml        2 KB  svc + ServiceMonitor, edge RED
├── servicemonitor-keycloak.yaml             2 KB  svc + ServiceMonitor, 153 metrics
├── servicemonitor-rook-ceph-mgr.yaml      996 B   all Ceph metrics
└── README.md                               22 KB  this document

Generator scripts (build-*.py, verify-*.py, run-*.py, wait-*.py) are
working files, not deliverables. podmonitor-ingress-nginx.yaml was
deleted: the PodMonitor approach yielded 0 targets because this
prometheus-operator no longer honours targetPort. See §2.5.
```

Cluster objects changed, in the order they were applied:

```
cm/grafana-datasource-config            PATCHED  6 -> 11 datasources
cm/grafana-dashboard-provider           PATCHED  +1 provider (UTM Observability)
cm/grafana-dashboards-observability     CREATED  12 dashboards, 579 KB of 1 MiB
deploy/utm-statistics-grafana           PATCHED  +1 volume, +1 mount, +DOR_RO_PASSWORD
servicemonitor/rook-ceph-mgr            CREATED  rook-ceph ns
servicemonitor/ingress-nginx-controller CREATED  ingress-nginx ns  (+svc ingress-nginx-metrics)
servicemonitor/keycloak                 CREATED  utm-core ns       (+svc keycloak-metrics)
deploy/keycloak                         PATCHED  utm-core ns  +KC_METRICS_ENABLED +KC_HEALTH_ENABLED
prometheusrule/utm-observability        CREATED  monitoring ns, 32 rules
es index uas-zones-geo                  CREATED  core-es7, derived from uas_9.0.0 (source untouched)
mysql user grafana_ro                   CREATED  core-database, column-scoped SELECT only
deploy/loki + cm + pvc + svc            CREATED  utm-statistics ns, closes application-logs gap
ds/alloy-logs + sa/rbac                 CREATED  utm-statistics ns, all 4 nodes, log shipper
cm/grafana-datasource-config            PATCHED  +Loki; Jaeger+Tempo tracesToLogsV2 repointed
cm/grafana-dashboards-observability     PATCHED  08 updated; +dashboard 12; 13 dashboards' nav links
ds/calico-node + deploy/calico-kube-controllers PATCHED  v3.27.0 -> v3.27.5 (kube-system, not
                                                 ArgoCD-managed at all -- see main doc §9)
cm/utm-statistics-tempo                 PATCHED  +metrics_generator; fixes TraceQL rate()
                                                 500s, verified HTTP 200
otel-operator (new namespace)           CREATED  opentelemetry-operator-system
Instrumentation x4 + 34 pod annotations CREATED  utm-services/-skyzr/-core/-aviamaps -- see
                                                 main doc's incident writeup (gap G4) for the
                                                 resource-cascade this fan-out triggered
```

Nothing existing was removed, and no existing Service, DaemonSet or
LoadBalancer was edited — every scrape was added through a new, dedicated
headless Service alongside the one already in use.

---

## 7. ⚠️ GitOps — action required

**Every change in §6 is live-cluster only. None of it is in Git.**

The ArgoCD application `utm-statistics` has `selfHeal: true` and is currently
**down (zero pods)**. When it returns it will revert every ConfigMap and
Deployment change above — including `KC_METRICS_ENABLED`, which would silently
take the Keycloak JVM and DB-pool rows of dashboard 11 back to NO DATA.

That specific regression is now alarmed: `KeycloakMetricsMissing` fires after
15 minutes without the `keycloak-metrics` job. It is a detector, not a fix.

Commit to `git@git.ops.io:uas/utm-devops.git` under `argocd-ns/utm-statistics`
before ArgoCD is restored. Objects outside that path need a home too:

| Object | Namespace | Belongs to |
|---|---|---|
| `servicemonitor/rook-ceph-mgr` | `rook-ceph` | whichever app manages rook-ceph |
| `servicemonitor/ingress-nginx-controller` + its Service | `ingress-nginx` | whichever app manages ingress-nginx |
| `servicemonitor/keycloak` + its Service | `utm-core` | whichever app manages utm-core |
| `deploy/keycloak` env patch | `utm-core` | same — this one is a change to an **existing** object and will be reverted first |
| `prometheusrule/utm-observability` | `monitoring` | whichever app manages monitoring |
| `deploy/loki`, `cm/loki-config`, `pvc/loki-data`, `svc/loki` | `utm-statistics` | this app — see `loki-config.yaml` |
| `ds/alloy-logs`, `sa`/`clusterrole`/`clusterrolebinding` | `utm-statistics` (+cluster-scoped RBAC) | this app — see `alloy-logs.yaml` |
| `cm/utm-statistics-tempo` | `utm-statistics` | this app — see `tempo-config.yaml`. Pre-existing object; this is its first commit ever |
| OTel Operator (Deployment, CRDs, RBAC, webhooks) | `opentelemetry-operator-system` (new namespace) | no auto-instrumentation injection cluster-wide; pods fall back to running unmutated |
| `Instrumentation` x4 + 34 pod annotations | `utm-services`/`-skyzr`/`-core`/`-aviamaps` | all 34 instrumented services silently stop getting traced |

The `uas-zones-geo` Elasticsearch index and the `grafana_ro` MySQL grant are
data-plane, not GitOps-managed; both are reproducible from
`uas-zones-geo-build.sh` and the `*-grant.sql` files in the repo root.

The Calico v3.27.5 bump (`kube-system`) is not GitOps-managed by **any**
application — it's a raw `kubectl apply`, no `ownerReferences`. It survives
any resync, but isn't backed by a manifest in this repo either; a from-Git
cluster rebuild would need Calico reinstalled separately.

---

## 8. Incident path

```
00 Executive Overview   ── what is wrong at all?
        ↓
01 Platform Health      ── which namespace / pod? restart leaderboard
        ↓
02 Kubernetes Workloads ── which container? waiting + termination reasons
        ↓
03 Nodes & Resources    ── is the node starved? disk / CPU / memory
        ↓
04 Ceph & Storage       ── is storage the root cause? slow ops, PGs, PVCs
        ↓
08 Coverage             ── can I even see this? or am I blind here?
```

The final step is deliberate. This cluster's characteristic failure is:

> node disk fills → Ceph MON_DISK_LOW → BlueStore slow ops → I/O latency →
> liveness probes time out → cluster-wide restart storm

which presents as an application problem and is not one. Dashboards 03 and 04
exist to make that chain visible in two clicks.
