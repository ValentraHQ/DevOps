# UTM Platform — Observability & Remediation Documentation

**Platform:** UTM (Unmanned Traffic Management)
**Cluster:** `uas` — Rancher-proxied, Kubernetes v1.30.14, 4 nodes
**Document date:** 2026-09-22
**Status:** Implemented on live cluster — **pending GitOps commit** (see §11)

---

## Table of contents

1. [Executive summary](#1-executive-summary)
2. [Platform inventory](#2-platform-inventory)
3. [Data source inventory](#3-data-source-inventory)
4. [Part A — Incident remediation](#4-part-a--incident-remediation)
5. [Part B — Drone Plan Dashboard](#5-part-b--drone-plan-dashboard)
6. [Part C — UTM Observability Suite](#6-part-c--utm-observability-suite)
7. [Infrastructure changes](#7-infrastructure-changes)
8. [Validation results](#8-validation-results)
9. [Observability gap register](#9-observability-gap-register)
10. [Alerting](#10-alerting)
11. [GitOps — outstanding actions](#11-gitops--outstanding-actions)
12. [Runbooks](#12-runbooks)
13. [Assumptions](#13-assumptions)
14. [Appendix A — file manifest](#14-appendix-a--file-manifest)
15. [Appendix B — reference queries](#15-appendix-b--reference-queries)

---

## 1. Executive summary

This engagement covered three connected pieces of work on the UTM platform:

| Part | Outcome |
|---|---|
| **A — Incident remediation** | Grafana restored from a permanent crash loop; five distinct defects fixed. Elasticsearch mapping defect corrected, recovering 92% of previously invisible data. |
| **B — Drone Plan Dashboard** | Existing dashboard rebuilt: 17 panels, datasource and query defects fixed. |
| **C — UTM Observability Suite** | New 10-dashboard, 164-panel enterprise observability suite with 21 alert rules. |

### The single most important finding

The platform's characteristic failure mode is a **cascade that presents as an application problem but is not one**:

```
node root disk fills (80%+)
   → Ceph MON_DISK_LOW
      → BlueStore slow operations
         → storage I/O latency
            → Kubernetes liveness probes time out
               → cluster-wide restart storm
```

Before this work, **none of the links in that chain were visible in Grafana**. Dashboards 03 and 04 exist specifically to make it traceable in two clicks.

### Headline discoveries

| Finding | Detail |
|---|---|
| Grafana had **never** been provisioned | ConfigMaps mounted to a path Grafana does not read. Zero datasources, zero dashboards, for the life of the deployment. |
| Grafana database was **ephemeral** | 9.8 GB PVC mounted at the wrong path; all state destroyed on every restart. |
| Ceph metrics **not scraped** | `rook-ceph-mgr` served 412 `ceph_*` metrics to nobody — no ServiceMonitor existed. |
| Prometheus datasource **broken** | Pointed at a Service name that does not exist. Dead for every consumer, not just new dashboards. |
| Elasticsearch **silently under-reporting** | An index with wrong mappings caused ES to return partial results; dashboards showed 3 of 37 documents. |
| UTM services **not instrumented** | 0 of 84 services expose Prometheus metrics. No RED metrics exist. |
| Application logs **do not exist** | The configured index has never been created. |

---

## 2. Platform inventory

### 2.1 Cluster

| Node | Role | IP | Status |
|---|---|---|---|
| utm-kubernetes-node-001 | control-plane | 192.168.70.11 | Ready |
| utm-kubernetes-node-002 | worker | 192.168.70.12 | Ready |
| utm-kubernetes-node-003 | worker | 192.168.70.13 | Ready |
| utm-kubernetes-node-004 | worker | 192.168.70.14 | Ready |

Kubernetes **v1.30.14**, containerd, Ubuntu 22.04. Storage: Rook-Ceph (3 HDD OSDs, 325 GB raw). Ingress: nginx. LoadBalancer: MetalLB. GitOps: ArgoCD.

### 2.2 UTM namespaces

| Namespace | Deployments | StatefulSets | ConfigMaps | Ingresses |
|---|---|---|---|---|
| `utm-core` | 6 | 3 | 13 | 3 |
| `utm-services` | 38 | 0 | 52 | 15 |
| `utm-skyzr` | 8 | 0 | 12 | 9 |
| `utm-aviamaps` | 2 | 0 | 4 | 1 |
| `utm-statistics` | 4 | 1 | 10 | 2 |
| `utm-devops` | 3 | 1 | 3 | 2 |
| **Total** | **61** | **5** | **94** | **32** |

**69 pods** across these namespaces, all visible to Prometheus via kube-state-metrics.

### 2.3 Key services in `utm-services` (38 deployments)

All images from `harbor.ops.io/utm-devops`. Notable: `operation-plan-service`, `conflict-detection`, `alerting-manager`, `telemetry-manager`, `airspace-data-collector`, `dynamic-geozone-atm`, `smartsis`, `ras-manager`, `cars`, `aixm`, `notam-geozone-adapter`, `user-portal`, `utm-manager`.

Legacy stacks still running: `service-registry-es5` (Elasticsearch 5), `service-registry-mysql5.7`, `elasticsearch5-config`.

---

## 3. Data source inventory

### 3.1 Two separate Elasticsearch clusters

This is a frequent source of confusion during incidents:

| Cluster | Service | Purpose |
|---|---|---|
| **core-es7** | `core-es7.utm-core.svc.cluster.local:9200` | Live operational data (plans, zones, flights, alerts) |
| **utm-statistics-es7** | `utm-statistics-es7.utm-statistics.svc.cluster.local:9200` | Statistics copies + Jaeger traces |

`operation-plan-service`, `oprs`, `es-migration` and `telemetry-manager` write to **core-es7**.
`utm-statistics-service` writes to **utm-statistics-es7**.

### 3.2 Datasets — verified counts

All counts from `_count`, never `_cat/indices docs.count`.

| Dataset | Cluster | `_count` | Time field | Data range |
|---|---|---|---|---|
| `uas_9.0.0` | core-es7 | **168** | `submitTime` | 2026-06-29 → 2026-08-04 |
| `operation-plan-wrapper` | core-es7 | **41** | `operationPlan.submitTime` | 2026-04-15 → 2026-05-12 |
| `operation-plan-result` | core-es7 | **24** | `operationPlanResult.startTime` | 2026-04-15 → 2026-05-12 |
| `ops-subscription` | core-es7 | **8** | *(none)* | n/a |
| `utm-generic-statistics` | utm-statistics-es7 | **635** | `timestamp` | 2026-03-23 → 2026-08-10 |
| `utm-op-statistics` | utm-statistics-es7 | **37** | `recordUpdateTime` | 2026-03-06 → 2026-05-12 |
| `utm-alerting-statistics` | utm-statistics-es7 | **0** | — | empty |
| `utm-services-logs*` | — | **index absent** | — | — |

> ### ⚠️ Nested-document inflation — read this before quoting any count
>
> `_cat/indices docs.count` counts **Lucene** documents, which includes nested
> sub-documents. Several UTM indices use `nested` types, so `_cat` massively
> overstates business counts:
>
> | Index | `_cat` says | Truth (`_count`) | Nested fields |
> |---|---|---|---|
> | `uas_9.0.0` | ~504 | **168** | `uasZone.geometry`, `laraAirspaceProperties.reservations` |
> | `operation-plan-wrapper` | 167 | **41** | `operationVolumes`, `aircraftInfos`, `uasRegistrations` |
> | `utm-op-statistics-000001-v2` | 126 | **34** | `aircraftInfos`, `operationVolumes` |
>
> **Always use `_count` or a parent-document aggregation.**

### 3.3 Prometheus

`kube-prometheus-stack` in namespace `monitoring`.

```
Total metric names : 1,415
Scrape targets     : 48 across 14 jobs
UTM pods visible   : 69
ServiceMonitors    : 18 (17 pre-existing + 1 added by this work)
```

Prometheus CR selectors — any new ServiceMonitor or PrometheusRule **must** carry `release: kube-prometheus-stack`:

```yaml
serviceMonitorSelector:          {matchLabels: {release: kube-prometheus-stack}}
serviceMonitorNamespaceSelector: {}      # all namespaces
ruleSelector:                    {matchLabels: {release: kube-prometheus-stack}}
```

---

## 4. Part A — Incident remediation

### 4.1 Grafana — five distinct defects

Grafana was in a permanent crash loop and had never served a dashboard.

#### Defect 1 — OAuth client secret never reached the container

Login failed with:

```
oauth2: "unauthorized_client" "Invalid client or Invalid client credentials"
```

Root cause: Secret `utm-statistics-grafana` held `GF_AUTH_GENERIC_OAUTH_CLIENT_SECRET`
(32 bytes) but **nothing referenced it**.

```
container.env[]   → 0 entries
envFrom           → [configMapRef] only
secret volumes    → uas-tls (CA cert) only
```

**Fix:**

```yaml
envFrom:
  - configMapRef:
      name: utm-statistics-grafana
  - secretRef:                      # ← added
      name: utm-statistics-grafana
```

#### Defect 2 — provisioning mounted to a path Grafana does not read

**This is why Grafana had no datasources and no dashboards, ever.**

```
GF_PATHS_PROVISIONING = /etc/grafana/provisioning          ← Grafana reads here (was EMPTY)
ConfigMaps mounted at   /usr/share/grafana/conf/provisioning/…   ← ignored
```

The log signature was a provisioning pass completing in **0.02 ms** — zero providers found.

**Fix:** both mount paths moved to `/etc/grafana/provisioning/{datasources,dashboards}`.
After the fix, provisioning took **6.46 s** and inserted 6 datasources.

#### Defect 3 — RWO Multi-Attach deadlock

`grafana-claim` is **ReadWriteOnce** (Ceph RBD) but the Deployment used
`RollingUpdate`. The new pod scheduled onto a different node and could never
attach the volume the old pod still held:

```
FailedAttachVolume: Multi-Attach error for volume "pvc-…"
Volume is already used by pod(s) utm-statistics-grafana-…
```

RollingUpdate keeps the old pod until the new one is Ready; the new one can
never become Ready without the volume. Earlier rollouts only succeeded by
chance, when the new pod happened to land on the same node.

**Fix:** `strategy: Recreate` — correct for any single-replica RWO-backed workload.

#### Defect 4 — liveness probe killed Grafana mid-migration

```
livenessProbe : tcpSocket:3000, initialDelay 30s, period 10s, failureThreshold 3
startupProbe  : (none)
```

That kills the container ~60 s after start. Grafana's schema migration took
longer, so it was killed mid-migration, restarted, and began again — forever.

**Fix:** `startupProbe` with `failureThreshold: 60, periodSeconds: 10` (10 min grace).

#### Defect 5 — the Grafana database was ephemeral

```
/var/lib/grafana/grafana.db   1.2 MB  on overlay      ← GF_PATHS_DATA, ephemeral
/usr/share/grafana/data/      24 KB   on /dev/rbd2    ← the 9.8 GB PVC, only lost+found
```

The PVC had been unused since 2026-04-08. Every restart destroyed all users,
preferences and annotations, and re-ran the full schema migration.

**Fix:** PVC remounted to `/var/lib/grafana`, **and** the init container's
`chown -R 1001:1001` corrected to **472** — the uid Grafana actually runs as.
Remounting without the uid fix would have produced an unwritable volume.

**Persistence proof:**

| | Fresh volume | After restart |
|---|---|---|
| `Executing migration` log lines | **757** | **0** |
| Time to Ready | 7 m 43 s | **24 s** |
| Sentinel file | written | **survived** |

### 4.2 Elasticsearch — silent 92% under-reporting

`utm-op-statistics-000001` (created 2026-03-17) was built with **dynamic
mappings** before a template existed. Indices from 2026-05-19 onward are
correct.

| Field | `000001` (34 docs) | `000002`–`000004` |
|---|---|---|
| `state`, `operator`, `modeOfOperation`, `sourceOrganization` | `text` | `keyword` |
| `calculatedCenterPoint` | `object {lat, lon}` | `geo_point` |

Elasticsearch answers an aggregation over a `text` field with a **partial
result**, not an error:

```
shards: {"total":4, "successful":3, "failed":1}
reason: "Text fields are not optimised for operations that require
         per-document field data like aggregations and sorting"
hits.total: 3          ← but _count says 37
```

Grafana renders that partial result without surfacing the failure. **Every
aggregation panel reported 3 documents when there were 37.**

#### Migration performed (non-destructive)

```
1. PUT  reindex-tmp-op-stats-000001        (corrected mapping, outside the
                                            utm-op-statistics* wildcard)
2. POST _reindex  000001 → tmp             → created 34, failures []
3. PUT  utm-op-statistics-000001-v2        (corrected mapping)
4. POST _reindex  tmp → 000001-v2          → created 34, failures []
5. POST utm-op-statistics-000001/_close    (CLOSED, not deleted — reversible)
```

The original index was **closed rather than deleted**. Closed indices are
excluded from wildcard expansion but retain their data; reopen with `_open`.

**Result:**

| | Before | After |
|---|---|---|
| Shards | ok=3 **failed=1** | ok=4 **failed=0** |
| Documents | 3 | **37** |
| Operators | 1 | **4** |
| Organisations | frequentis | **frequentis, flyk** |
| Mode of operation | BVLOS only | **VLOS 24 / BVLOS 13** |
| Map locations | 1 | **14** |
| Flight distance | 0.0 km | **40.44 km** |

An entire second organisation (`flyk`) and the majority operation mode (VLOS)
had been absent from every panel.

---

## 5. Part B — Drone Plan Dashboard

The pre-existing dashboard (`uid: elasticsearch`) had multiple defects.

| Defect | Detail | Fix |
|---|---|---|
| Datasource uid did not exist | Panels referenced `uid: "elasticsearch"`; the real uid is `bdq1z7uvssu80d` | Pinned all panels to the real uid |
| Export-format file | Provisioned JSON contained `__inputs`/`__requires`/`__elements` and `${DS_ELASTICSEARCH}`. File provisioning **does not resolve `__inputs`** — that is UI-import only | Re-authored as plain dashboard JSON |
| "Overall" stat | 30d `date_histogram` + `lastNotNull` = last bucket only, not a total | Single-bucket aggregation |
| "Top 20 Operators" | `size: "10"` | 20 |
| Duplicate panels | Two identical "Drone Plan Table" panels | One `raw_data` table |
| Table aggregation | 5–6 stacked `terms` aggs produced a cartesian bucket product, not one row per plan | `raw_data` |
| `sortBy` | Referenced `operationPlan.submitTime`, a field that does not exist | `submitTime` |
| Unscoped queries | `query: ""` searched `utm-alerting-statistics*` **and** `utm-op-statistics*` | `_index: utm-op-statistics*` |
| Axis mismatch | Bucketed `startTime` while filtering on `recordUpdateTime` | Both on `recordUpdateTime` |
| Empty `bucketAggs` | Grafana's ES plugin rejects a metric query with no bucket agg → `received invalid query` | 1-year single bucket |
| Time range | `now-30d` against data ending 2026-05-12 → permanently empty | `now-1y` |

**Result:** 17 panels including 8 new ones built from previously unused fields
(`flightDistanceInKm`, `actualDurationInSeconds`, `reservedAreaSizeInSquareMeters`,
`priority.priorityLevelSimple`, `maximumAltitude` histogram, requested-vs-actual
duration).

> **Nested-field limitation:** `operationVolumes` and `aircraftInfos` are
> `nested` types. Grafana's Elasticsearch datasource cannot aggregate inside
> nested fields, so `isBVLOS`, `maxTakeoffMassGrams` and `hasDangerousPayload`
> are unreachable from Grafana without denormalising them onto the parent
> document at ingest.

---

## 6. Part C — UTM Observability Suite

**12 dashboards, 272 panels**, Grafana folder **UTM Observability**.

| # | Dashboard | UID | Panels | Refresh | Default range |
|---|---|---|---|---|---|
| 00 | Executive Platform Overview | `utm-obs-00-executive` | 30 | 30s | 6h |
| 01 | Platform Health | `utm-obs-01-health` | 9 | 30s | 6h |
| 02 | Kubernetes Workloads | `utm-obs-02-workloads` | 13 | 30s | 6h |
| 03 | Nodes & Cluster Resources | `utm-obs-03-nodes` | 9 | 30s | 6h |
| 04 | Ceph & Storage Health | `utm-obs-04-ceph` | 22 | 30s | 6h |
| 05 | UAS Zones & Airspace | `utm-obs-05-uas` | 23 | 5m | 1y |
| 06 | Operation Plan Lifecycle | `utm-obs-06-opplans` | 26 | 1m | 1y |
| 07 | User Activity & Adoption | `utm-obs-07-users` | 12 | 5m | 1y |
| 08 | Observability Coverage | `utm-obs-08-coverage` | 21 | 5m | 6h |
| 09 | Drone Plan Dashboard *(statistics)* | `utm-drone-plans` | 17 | 5m | 1y |
| 10 | DOR / DMS Registry | `utm-obs-10-dor` | 37 | 5m | 1y |
| 11 | Keycloak Identity | `utm-obs-11-keycloak` | 53 | 5m | 30d |

### 6.1 Design principles

1. **Built only from verified metrics and fields.** Every PromQL expression was
   executed against Prometheus, and every Elasticsearch field confirmed via
   `_mapping` / `_field_caps` / aggregation, before being written into a panel.
2. **Gaps are declared, never implied.** Where a field is empty or a capability
   missing, a text panel states it with evidence. An empty chart is never used
   to represent a missing capability.
3. **No data ≠ zero ≠ not monitored.** Threshold bases are neutral, so a
   no-data panel can never render green. Every field config carries
   `noValue: "NO DATA"`.
4. **Colour communicates state only.** Severity: Healthy / Warning / High /
   Critical / Unknown. No decorative colour.
5. **Historical datasets are labelled as such.** Dashboards 05–07 default to a
   1-year range, and the Executive dashboard's business KPIs carry a per-panel
   `timeFrom: 1y` override with a visible relative-time indicator.

### 6.2 Dashboard notes

**00 — Executive Platform Overview.** Four KPI rows (Cluster, Availability &
Restarts, Resource Pressure, UTM Business Data) plus trends. Business KPIs
override the dashboard time range because their datasets are historical.

**01 — Platform Health.** The primary operational dashboard. The **Pod Restart
Leaderboard** shows the cumulative counter *and* the 1h rate together — a high
total with a zero rate is historical damage that has stabilised; a non-zero rate
is an active crash loop. Also: deployment availability with a computed
percentage, and a namespace rollup for drill-down.

**02 — Kubernetes Workloads.** CPU, memory, network, CPU throttling, and the two
tables that matter during an incident: **container waiting reasons** and **last
termination reasons** (OOMKilled vs Error).

**03 — Nodes & Cluster Resources.** Node disk leads the dashboard deliberately —
it is the head of this cluster's failure cascade. Thresholds 70 / 80 / 90 %.

**04 — Ceph & Storage Health.** Health status, OSD state and latency, MON quorum,
PG states, slow ops, pool utilisation and PVC utilisation.
*Required an infrastructure fix to exist at all — see §7.1.*

**05 — UAS Zones & Airspace.** ED-269 geozones. **These are airspace zones, not
drones.** Two explicit gap panels: ED-269/LARA extended properties are
unpopulated, and geometry is not mappable.

**06 — Operation Plan Lifecycle.** Live plans by state, result outcomes,
operations profile. Documents the live-vs-statistics comparison and the
`ops-subscription` limitation.

**07 — User Activity & Adoption.** Front-end telemetry from `flyk-web`.
Anonymised identifiers only. Gap panel for unpopulated user dimensions.

**08 — Observability Coverage.** The most important dashboard for honesty —
states exactly what is and is not observable, with evidence.

---

## 7. Infrastructure changes

Only what the dashboards required. **No** new Elasticsearch, Prometheus, Ceph
exporter or logging stack was deployed.

### 7.1 ServiceMonitor for `rook-ceph-mgr` — required, and a correction

The original brief stated Ceph metrics were already scraped. **They were not.**

```
ceph_* metric names in Prometheus      : 0
rook*  metric names in Prometheus      : 0
ServiceMonitors in rook-ceph namespace : 0
```

All 17 pre-existing ServiceMonitors were in `monitoring` and targeted the
monitoring stack itself, while `rook-ceph-mgr:9283` served **412 `ceph_*`
metrics** to nobody.

`servicemonitor-rook-ceph-mgr.yaml` scrapes that **existing** endpoint. After
applying, `ceph_health_status` went from absent to `1` (HEALTH_WARN).

### 7.2 core-es7 datasources — three, not one

| uid | Index | Time field |
|---|---|---|
| `core-es7-uas` | `uas_9.0.0` | `submitTime` |
| `core-es7-opw` | `operation-plan-wrapper*` | `operationPlan.submitTime` |
| `core-es7-opr` | `operation-plan-result*` | `operationPlanResult.startTime` |

All point at `core-es7.utm-core.svc.cluster.local:9200`, in-cluster only.

**Why three:** Grafana's Elasticsearch datasource binds exactly one index
pattern **and one time field** per datasource. These indices use three different
time fields, so one datasource cannot serve them. A Grafana constraint, not a
preference.

### 7.3 Prometheus datasource URL — pre-existing defect

```diff
- url: http://prometheus.monitoring.svc.cluster.local:9090              # no such Service
+ url: http://kube-prometheus-stack-prometheus.monitoring.svc.cluster.local:9090
```

There is no Service named `prometheus` in `monitoring`. **This datasource was
dead for every consumer**, not only the new dashboards — any pre-existing panel
or alert using it was silently returning nothing. Worth auditing anything else
that referenced it.

### 7.4 Dashboard provisioning

- `provider.yaml` — single provider → folder **UTM Observability**,
  path `/var/lib/grafana/dashboards-observability`, `allowUiUpdates: false`
  (GitOps-managed; UI edits would be silently overwritten).
- `configmap-dashboards.yaml` — 12 dashboards, 579 KB of the 1 MiB ConfigMap limit.
- `deployment-patch.json` — volume + volumeMount.

> **Apply with `kubectl apply --server-side`.** A normal `apply` stores the whole
> object in the `last-applied-configuration` annotation and exceeds the 256 KB
> annotation limit.

### 7.5 Summary of cluster objects changed

```
cm/grafana-datasource-config         PATCHED  6 -> 11 datasources, Prometheus URL fixed
cm/grafana-dashboard-provider        PATCHED  +1 provider
cm/grafana-dashboards-observability  CREATED  12 dashboards, 579 KB
cm/grafana-dashboard-provisioning    PATCHED  Drone Plan Dashboard rebuilt
deploy/utm-statistics-grafana        PATCHED  secretRef, mount paths, Recreate,
                                              startupProbe, PVC path, init chown,
                                              +observability volume, +DOR_RO_PASSWORD
servicemonitor/rook-ceph-mgr         CREATED  rook-ceph namespace
svc + servicemonitor/ingress-nginx   CREATED  ingress-nginx namespace -- edge RED metrics
svc + servicemonitor/keycloak        CREATED  utm-core namespace -- 153 runtime metrics
deploy/keycloak                      PATCHED  utm-core: +KC_METRICS_ENABLED +KC_HEALTH_ENABLED
prometheusrule/utm-observability     CREATED  monitoring namespace, 32 rules
es: utm-op-statistics-000001         CLOSED   (reversible)
es: utm-op-statistics-000001-v2      CREATED  corrected mappings, 34 docs
es: uas-zones-geo                    CREATED  derived centroids; uas_9.0.0 untouched
mysql: grafana_ro @ core-database    CREATED  column-scoped SELECT, no PII columns
deploy/loki + cm/loki-config + pvc   CREATED  utm-statistics -- closes G2 (see 9)
                                              image re-hosted harbor.ops.io/utm-devops/loki:3.7.8
                                              (nodes have no direct docker.io route)
ds/alloy-logs + sa/clusterrole(binding) CREATED utm-statistics -- log shipper, all 4 nodes
                                              image re-hosted harbor.ops.io/utm-devops/alloy:v1.20.1
cm/grafana-datasource-config          PATCHED  +1 datasource (Loki, uid loki-uid);
                                              Jaeger + Tempo tracesToLogsV2.datasourceUid
                                              repointed es-logs-uid -> loki-uid -- closes G3
cm/grafana-dashboards-observability   PATCHED  08 panel 110/121 updated to AVAILABLE;
                                              +dashboard 12 (Application Logs, namespace
                                              variable); all 13 dashboards' nav links updated
ds/calico-node + deploy/calico-kube-controllers PATCHED v3.27.0 -> v3.27.5 (kube-system,
                                              not ArgoCD-managed -- see 9 platform issues)
cm/utm-statistics-tempo               PATCHED  +metrics_generator (local-blocks, inmemory
                                              ring) -- fixes TraceQL rate()/empty-ring 500s,
                                              closes G4b. First time this ConfigMap has a
                                              local file backing it (tempo-config.yaml, new)
```

Nothing was deleted.

---

## 8. Validation results

### 8.1 Prometheus

```
Total metric names : 1,415
UTM pods visible   : 69      ✅
Nodes / Ready      : 4 / 4
Scrape targets     : 48 (14 jobs)
```

| Query | Result |
|---|---|
| `count(kube_node_info)` | 4 |
| `count(kube_pod_info{namespace=~"utm-.*"})` | 69 |
| `kube_pod_container_status_restarts_total` | 200 series |
| `kube_deployment_status_replicas{,_available,_unavailable}` | 104 series each |
| `kube_node_status_condition` | 60 series |
| `container_{cpu_usage_seconds_total,memory_working_set_bytes}` | 1,003 series each |
| `kubelet_volume_stats_{used,capacity}_bytes` | 42 series each |
| `kube_ingress_info{namespace=~"utm-.*"}` | **32** |
| `kube_service_info{namespace=~"utm-.*"}` | **84** |
| `probe_success` | 3 series |
| **max node root-filesystem utilisation** | **80.96 %** ⚠️ |

### 8.2 Ceph — after the ServiceMonitor

| Query | Result |
|---|---|
| `ceph_health_status` | **1** (HEALTH_WARN) |
| `sum(ceph_osd_up)` / `sum(ceph_osd_in)` | 3 / 3 |
| `sum(ceph_mon_quorum_status)` | 3 |
| `sum(ceph_healthcheck_slow_ops)` | 0 |
| `sum(ceph_pg_clean)` / `sum(ceph_pg_total)` | 81 / 81 |
| `ceph_cluster_total_bytes` / `_used_bytes` | 325 GB / 37.4 GB |
| `max(ceph_osd_apply_latency_ms)` | 121 ms |
| `ceph_osd_stat_bytes_used` | **EMPTY** — see gaps |

### 8.3 Elasticsearch connectivity from the Grafana pod

```
uas_9.0.0/_count                 {"count":168, "_shards":{"failed":0}}
operation-plan-wrapper*/_count   {"count":41,  "_shards":{"failed":0}}
operation-plan-result*/_count    {"count":24,  "_shards":{"failed":0}}
```

### 8.4 Provisioning

```
inserting datasource from configuration name=core-es7-uas  uid=core-es7-uas
inserting datasource from configuration name=core-es7-opw  uid=core-es7-opw
inserting datasource from configuration name=core-es7-opr  uid=core-es7-opr

/var/lib/grafana/dashboards-observability/ → 9 files
provisioning.dashboard start→finish       = 6.46 s (a real import)
level=error lines                         = 0
pod                                       = 1/1 Running
```

### 8.5 Confirmed field values used to build panels

```
uas_9.0.0
  uasZone.type              REQ_AUTHORIZATION 130, NO_RESTRICTION 38
  uasZone.country           SWE 131, EST 37
  uasZone.reason            AIR_TRAFFIC 168
  dataSource.originator     AIXM-import 131, uas 37

operation-plan-wrapper
  operationPlan.state       CLOSED 14, PROPOSED 14, AUTHORIZED 10, ACTIVATED 3
  modeOfOperation           VLOS 28, BVLOS 13
  operator                  PH-DRN-REG-AX1B2 35, FRQ 6

operation-plan-result
  state                     GRANTED 13, TIMEOUT 7, DENIED 4
  resultType                AUTHORIZATION 14, ACTIVATION 10

utm-generic-statistics
  application               flyk-web 635
  event                     pageload 298, visibilitychange 298,
                            deconflict 21, plan 18
  unique userIdAnonymized   5
```

> **Operational note:** `operation-plan-result` shows **TIMEOUT on 7 of 24
> (29%)** of authorisation/activation attempts. This warrants investigation by
> the operations team.

### 8.6 Loki / log pipeline

```
Grafana datasource health check   {"message":"Data source successfully connected.","status":"OK"}
alloy-logs DaemonSet               4/4 Running (node-004 included -- no Ceph CSI dependency)
Manual push/query round trip       HTTP 204 on push; exact line returned on query_range
Real cluster data confirmed        {namespace="utm-statistics"} returned live entries from
                                    utm-services, monitoring, etc. across multiple nodes
Backfill-on-first-discovery proven cars (utm-services) log line timestamped BEFORE alloy-logs
                                    itself started was present in Loki -- confirms Alloy reads
                                    existing file content on first discovery, not tail-only
```

**Initial "missing pods" investigation (2026-10-02):** a user report of pods
missing from logs traced to a flawed check -- `label/pod/values` without
explicit `start`/`end` silently uses a narrow window and under-reports. The
correct check is the `/series` API with explicit bounds. Re-run that way,
every pod across all 6 `utm-*` namespaces that has **ever** written to
stdout/stderr is present in Loki. The only three absent (`utm-core/dns-test`,
`utm-services/service-registry-mysql`, `utm-services/smartsis-client`) have
**zero** log output via plain `kubectl logs` too, independent of Loki
entirely -- a property of those containers, not a pipeline gap.

---

## 9. Observability gap register

| # | Gap | Status | Evidence | To close |
|---|---|---|---|---|
| G1 | Application RED metrics | `NOT INSTRUMENTED` | 0 `http_*`/`grpc_*` series from utm-*; 85 services. **1** ServiceMonitor now targets utm-* (`utm-core/keycloak`) but scrapes Keycloak's JVM and DB pool, not application requests | Micrometer/actuator + ServiceMonitor per service. Partial substitute already in place: ingress-edge RED via `nginx_ingress_controller_*` |
| G1b | Keycloak login / token counters | `NOT INSTRUMENTED` | `keycloak_*` and `http_server_requests_*` absent from the 153-line metrics endpoint | Add the `keycloak-metrics-spi` extension to the container image; it is not bundled and is **not** enabled by `KC_METRICS_ENABLED` |
| G2 | Application logs | `RESOLVED` (2026-10-02) | Loki + `alloy-logs` DaemonSet deployed; health check OK; real log data verified flowing from multiple namespaces, all 4 nodes. See §8.6. Note: `utm-services-logs*` (ES, app-level structured logs) is a **separate**, still-genuinely-absent index — Loki doesn't close that one | — |
| G3 | Jaeger/Tempo trace→log link | `RESOLVED` (2026-10-02) | `tracesToLogsV2.datasourceUid` on both Jaeger and Tempo repointed `es-logs-uid` → `loki-uid` | — |
| G4 | Distributed tracing | `FANNED OUT, CLUSTER RECOVERING` (2026-10-02) | Was `STALE` (newest span `2026-04-14`), then `PILOT IN PROGRESS`. OTel Operator v0.160.0 (`otel-operator.yaml`) + `Instrumentation` CRs in 4 namespaces (`otel-instrumentation*.yaml`) targeting Tempo's OTLP HTTP receiver (`:4318`). **34 services now instrumented** via `instrumentation.opentelemetry.io/inject-{java,nodejs}` annotations, zero app code changes: the 2-service pilot (verified first, real traces confirmed including organic background activity) + a 32-service fan-out across `utm-services` (28), `utm-skyzr` (3), `utm-core` (`geoserver`), `utm-aviamaps` (`flyk`). The fan-out itself triggered a serious cascading incident — see the dedicated writeup immediately below this table. `cars` (Rust) has no viable auto-instrumentation path; would need source changes, not attempted. Keycloak (3 instances) deliberately excluded as identity-critical infra. `utm-devops` has nothing eligible (nginx frontends + Vault, both Go/static) | Let the cluster finish settling from the 2026-10-02 incident (see below) before declaring this fully done; re-verify trace flow across all 34 once stable |
| G4b | TraceQL metrics queries (`\| rate()`, `\| compare()`, etc.) | `RESOLVED` (2026-10-02) | Was `500: ...empty ring` (rate) and separately `500: ...syntax error: unexpected IDENTIFIER` on `compare()` (needs Tempo ≥2.6; was on 2.5.0). Fixed: `local-blocks` processor + `ring.kvstore.store: inmemory` + `filter_server_spans: false` + `flush_to_storage: true` (`tempo-config.yaml`), and image bumped `2.5.0` → `2.10.8` (latest 2.x — **not** 3.x: Tempo 3.0 moved metrics-generator to require Kafka, which this cluster doesn't have). Verified both `rate()` and `compare()` return `HTTP 200` with `completedJobs`/`totalJobs` matching, no errors. Result series is still empty — **expected**, same root cause as G4: zero trace data exists in any recent window because nothing currently exports traces. Fixing *that* (re-instrumenting `utm-services` apps) is separate, larger work, not started |
| G5 | Endpoint coverage | `PARTIAL ~9%` | 3 blackbox targets vs 32 ingresses | Extend blackbox targets |
| G6 | Blackbox probes | `ALL FAILING` | `sum(probe_success)` = **0** of 3 | Investigate the 3 targets |
| G7 | Control-plane metrics | `NOT SCRAPED` | 7 targets down: `kube-etcd`, `kube-scheduler`, `kube-controller-manager`, `kube-proxy` ×4 | Bind components to a scrapeable address |
| G8 | Alert history | `NO DATA` | `utm-alerting-statistics` = 0 docs in all 4 indices | Use Grafana/Prometheus alerting instead |
| G9 | Per-OSD Ceph capacity | `NOT EXPOSED` | `ceph_osd_stat_bytes_used` empty on mgr:9283 | ServiceMonitor for `rook-ceph-exporter:9926` |
| G10 | ED-269 / LARA properties | `NO DATA` | `uSpaceClass`, `restrictionCondition`, `isLaraAirspace` all 0 buckets | Confirm ingest expectations |
| G11 | UAS zone geometry | `RESOLVED` | `geo_shape` inside a `nested` field, which Grafana can aggregate neither of | Closed: `uas-zones-geo-build.sh` derives a parent-level centroid `geo_point` into `uas-zones-geo`; the source index is not modified |
| G12 | `ops-subscription` | `NOT QUERYABLE` | All fields `text`, `aggregatable: false`; no date field | Add `keyword` sub-fields + timestamp |
| G13 | User dimensions | `NO DATA` | `location`, `userRoles`, `param1/2` empty; `userAgent` non-aggregatable `text` | Confirm with telemetry owner |

### Incident: 2026-10-02 OTel fan-out resource cascade

**Status as of this write-up: cluster recovering, not fully settled.** Documented
honestly rather than claimed resolved, per this document's own discipline of
not marking things done before they're verified.

**Trigger.** `instrument-all.ps1` restarted 34 Deployments across 4
namespaces (`utm-services`, `utm-skyzr`, `utm-core`, `utm-aviamaps`)
simultaneously, to apply OTel auto-instrumentation annotations from the
already-verified 2-service pilot. Each restart briefly runs old+new pod
together (RollingUpdate surge); doing this for 34 services at once was not
checked against actual per-node memory headroom first. **This was a mistake
in the rollout plan, not an execution error** — the commands ran exactly as
written.

**Cascade, in order:**
1. Kubernetes scheduler began reporting `FailedScheduling: Insufficient cpu,
   Insufficient memory` on multiple nodes as surge pods piled up.
2. `utm-kubernetes-node-002` — already hosting a disproportionate share of
   Java services (Loki, `cars`, `ras-manager`, `smartsis`, `dor-keycloak`,
   `core-es7-1`) before this even started — hit genuine host-level memory
   exhaustion. The **Linux kernel OOM killer fired twice**, killing `java`
   processes directly (confirmed via `SystemOOM` node events, not just a
   Kubernetes-level eviction). Node-002 went `NotReady`, flapped
   `Ready`→`NotReady` repeatedly over roughly 10 minutes.
3. Several **previously stable, multi-day-uptime pods** (`alerting-manager`,
   `cme-manager`, `drone-flight-service`, `operation-plan-service`) —
   services this incident never touched directly — also crashed, caught as
   collateral damage from the same host-level pressure.
4. The instability then **moved to `utm-kubernetes-node-003`**, which also
   went `NotReady`. This ruled out "node-002 is just uniquely weak" as the
   explanation — it's a cluster-wide capacity ceiling, and whichever node
   currently absorbs the restart burst gets pushed over it.
5. `core-es7` (Elasticsearch, `utm-core`) lost 2 of its 3 replicas as
   collateral damage — `core-es7-1` (node-002, exit 143/SIGTERM) and
   `core-es7-2` (node-003, exit 137/SIGKILL — the OOM signature). With 2 of
   3 ES nodes bouncing, the cluster **lost quorum and went `status: red`**:
   `active_primary_shards: 0`, `142 unassigned_shards`, a task waiting
   **238 seconds** in queue. This is why `environment-manager` (and likely
   other ES-dependent services) kept crash-looping on
   `SocketTimeoutException` against ES — not a bug in them, ES genuinely had
   zero readable shards for several minutes.
6. Ceph (`rook-ceph`) also shows **`HEALTH_WARN`, degraded data redundancy**
   (confirmed via `ceph -s`: at worst 33.3% of objects degraded, all 81 PGs
   `active+undersized+degraded`) — almost certainly the same root cause,
   since node-002 hosts both a Ceph mon (`rook-ceph-mon-bj`) and an OSD
   (`osd.3`).

**Recovery, confirmed in progress:** ES climbed `red` → `yellow`,
0% → 92.5% active shards, 238s → 0s queue wait, then **dipped again** to
66.7%/2-of-3-nodes-visible before settling further — recovery has been
non-monotonic, not a clean straight line. Ceph's degraded-object percentage
dropped from 33.3% to 0.162% over the same window. All 4 Kubernetes nodes
currently show `Ready`, but given the flapping pattern already observed
twice, that is not being treated as proof of full stability on its own.

**Decisions made, not yet executed:**
- A proposal to add a 4th `core-es7` replica on `utm-kubernetes-node-001`
  (the control-plane node) was **rejected**: `core-es7`'s existing
  `StatefulSet` already carries a hard `requiredDuringScheduling`
  `nodeAffinity` excluding the control-plane node — a deliberate pre-existing
  safeguard — and node-001 is independently already overcommitted
  (memory limits at 148%, hosting `etcd`/`kube-apiserver` and the full
  Rancher management stack). Putting ES there would compete with `etcd` for
  memory on a node this cluster cannot afford to lose.
- Agreed path instead: scale `core-es7` to 4 replicas across the 3 worker
  nodes only, **after** increasing RAM/CPU on `utm-kubernetes-node-002` and
  `utm-kubernetes-node-003` (the two that proved unable to absorb load
  during this incident; node-004 has real headroom and needs no change).
  This requires hypervisor-level VM resizing, outside `kubectl` access —
  staged as a `cordon` → `drain` → (operator resizes VM) → verify →
  `uncordon` sequence, one node at a time, not simultaneously.
- The node-002 drain was **paused before execution**: Ceph was independently
  found already degraded at the moment the drain was about to run, and
  draining node-002 would have additionally removed one of three Ceph mons
  and one of three OSDs from an already-degraded pool. Resuming is
  conditional on Ceph reporting healthy (not just improving) first.

**Lessons learned, for next time:**
1. Before restarting N services at once, check actual per-node memory
   *limits* allocation (not just requests) — node-002 was already at 148%
   memory-limit overcommitment before this incident started, which was
   knowable in advance and wasn't checked.
2. Stagger mass restarts in small batches (5–8 services), not 34 at once,
   specifically because RollingUpdate surge roughly doubles a service's
   momentary footprint.
3. A simultaneous restart's blast radius isn't bounded to the services being
   restarted — shared backends (ES, Ceph) sitting on the same nodes absorb
   the same pressure and can fail as collateral damage, as happened here.

### Unrelated but outstanding platform issues

| Issue | Detail |
|---|---|
| **ArgoCD down** | Zero pods in the `argocd` namespace. All applications show blank sync/health. |
| **Node disks 71–80%** | Head of the failure cascade. `max` currently **80.96%**. |
| **Zabbix down ~202 days** | `postgres-pvc` does not exist → postgres Pending → server CrashLoop (5,389 restarts) → web CrashLoop (15,046). Unnoticed because Zabbix *is* the monitoring. |
| **dor-acg Keycloak 401** | `utm-skyzr/dor-acg`, 3,013 restarts. Stale client credential. |
| **Chronic crashloopers** | `dynamic-geozone-atm` (4,987), `telemetry-manager` (3,781), `airspace-data-collector` (3,260); control plane `kube-controller-manager` (1,950), `kube-scheduler` (1,851). |
| **Ceph crashes** | Unacknowledged since March, including `osd.2`, an OSD that no longer exists. |
| **kubeadm certs expired 2026-09-30** | All 11 certs hit the kubeadm 365-day default simultaneously; `kubectl exec/logs/top` failed cluster-wide, controller-manager/scheduler crash-looped. **Fixed** (2026-09-30) via `kubeadm certs renew all` + static-pod restart on node-001; verified end-to-end, certs now expire 2027-09-30. No renewal automation exists yet -- same incident will recur in 2027 absent one. |
| **Calico confd/Typha watch-desync, cluster-wide** | Every node's `calico-node` restarts frequently (120-253 restarts each, not node-004-specific as first assumed) on a known bug: `/calico/ipam/...` watch gets `too old resource version` and never cleanly resyncs, destabilizing calico-node roughly every ~24h. Each restart forces `SandboxChanged` on that node's other pods, which occasionally collides with a separate runc/containerd bug (stale `/dev/termination-log` mountpoint) -- this is what produced the high rook-ceph CSI nodeplugin restart counts, previously misattributed to node-004 alone. **Partially addressed:** bumped Calico v3.27.0 -> v3.27.5 (2026-10-02, image-only change, zero RBAC/config drift, verified). This does **not** fix the root cause -- the real fix (Typha reconnection logic) lands only in v3.31, which requires migrating off the raw-manifest install to the Tigera operator per Calico's own upgrade guidance. Deferred as its own planned piece of work; not ArgoCD-managed (raw `kubectl apply`, kube-system). |
| **Three containers never log anything** | `utm-core/dns-test`, `utm-services/service-registry-mysql`, `utm-services/smartsis-client` have zero stdout/stderr output in their entire lifetime (weeks-months), confirmed independent of Loki. Not investigated further -- noted in case it's unexpected to the service owners. |

---

## 10. Alerting

Delivered as a `PrometheusRule` reusing the existing kube-prometheus-stack
Alertmanager. **32 rules in 6 groups.**

No rule is derived from `utm-alerting-statistics` — that index has 0 documents,
so any rule on it would be permanently silent while appearing configured.

### 10.1 Critical (9)

`UTMNodeNotReady` · `UTMNodeDiskCritical` (>90%) · `UTMCephUnhealthy` ·
`UTMCephOSDDown` · `UTMCephMonQuorumLost` · `UTMDeploymentUnavailable` ·
`UTMExcessivePodRestarts` (>20/h) · `UTMContainerOOMKilled` ·
`UTMPVCCriticallyFull` (>90%)

### 10.2 Warning (10)

`UTMPodRestartRateRising` · `UTMNodeDiskWarning` (>80%) · `UTMNodeMemoryPressure` ·
`UTMCephHealthWarn` · `UTMCephSlowOps` · `UTMCephOSDHighLatency` (>500 ms) ·
`UTMDeploymentDegraded` · `UTMPodPending` · `UTMEndpointDown` ·
`UTMPrometheusTargetDown`

### 10.3 Coverage (2)

`UTMCephMetricsMissing` and `UTMKubeStateMetricsMissing` — both use `absent()`.
These are meta-alerts: they fire when **monitoring itself** breaks, because the
absence of a signal must not be mistaken for health.

### 10.4 Identity — critical (3)

`KeycloakDBPoolExhausted` (threads queueing for a DB connection) ·
`KeycloakWorkerPoolSaturated` (>10 queued requests) · `KeycloakTasksRejected`
(users turned away outright).

### 10.5 Identity — warning (6)

`KeycloakDBPoolSaturating` (>90% of pool in use) · `KeycloakHeapHigh` (>85%) ·
`KeycloakGCOverheadHigh` (>10% of CPU in GC) · `KeycloakConnectionChurn` ·
`KeycloakConnectionLeak` · `KeycloakRestarted`.

### 10.6 Identity — coverage (2)

`KeycloakMetricsMissing` and `KeycloakScrapeFailing`. The first exists
specifically because `KC_METRICS_ENABLED` is **not yet in Git**: an ArgoCD
resync would strip it and silently return dashboard 11's runtime rows to
NO DATA. The alert detects that; it does not prevent it.

Deliberately **not** written: any rule on Keycloak login rate, login failures
or token issuance. Those counters do not exist in this image, and a rule on an
absent series is permanently silent while appearing configured.

Thresholds were set against measured idle behaviour on 2026-09-24: heap
utilisation 0.046, GC overhead 0.0, pool high-water mark 2, awaiting 0,
worker queue 0, rejected 0. All 32 rules report `health=ok` in
Prometheus `/api/v1/rules`.

### 10.7 Currently firing

| Alert | Instances |
|---|---|
| `UTMPrometheusTargetDown` | 7 |
| `UTMEndpointDown` | 3 |
| `UTMPodRestartRateRising` | 3 |
| `UTMNodeDiskWarning` | 1 |
| `UTMCephHealthWarn` | 1 |
| `UTMDeploymentUnavailable` | 1 |
| `UTMDeploymentDegraded` | 1 |

All 17 instances are **genuine pre-existing conditions** that were previously
invisible, not false positives from the new rules.

---

## 11. GitOps — outstanding actions

> ### ⚠️ Every change in this document is live-cluster only
>
> ArgoCD application `utm-statistics` has `selfHeal: true` and is **currently
> down with zero pods**. When it is restored it will **revert every ConfigMap
> and Deployment change** described here.

**Repository:** `git@git.ops.io:uas/utm-devops.git`
**Path:** `argocd-ns/utm-statistics`

### Must be committed before ArgoCD returns

| # | Change | File |
|---|---|---|
| 1 | Grafana `secretRef` in `envFrom` | deployment |
| 2 | Provisioning mount paths → `/etc/grafana/provisioning` | deployment |
| 3 | `strategy: Recreate` | deployment |
| 4 | `startupProbe` | deployment |
| 5 | PVC → `/var/lib/grafana` + init `chown 472:0` | deployment |
| 6 | Observability volume + mount | `deployment-patch.json` |
| 7 | Datasources: +3 core-es7, Prometheus URL fix | `datasource.yaml` |
| 8 | Dashboard provider | `provider.yaml` |
| 9 | Observability dashboards ConfigMap | `configmap-dashboards.yaml` |
| 10 | Drone Plan Dashboard | `drone-plan-dashboard-enhanced.json` |
| 11 | Grafana `DOR_RO_PASSWORD` env (secret `grafana-dor-ro`) | `deployment-patch-dor.json` |
| 17 | Loki deployment (ConfigMap + PVC + Deployment + Service) | `loki-config.yaml` |
| 18 | Alloy log shipper (SA + ClusterRole/Binding + ConfigMap + DaemonSet) | `alloy-logs.yaml` |
| 19 | Loki datasource; Jaeger/Tempo `tracesToLogsV2` repoint | `datasource.yaml` |
| 20 | Dashboard 08 panel 110/121 update + new dashboard 12 + nav links | `configmap-dashboards.yaml` equivalent (12 files updated + `12-application-logs.json` new) |
| 21 | Tempo `metrics_generator` config (fixes TraceQL `rate()` 500s) | `tempo-config.yaml` — also the first commit of this ConfigMap at all; it predates this repo and was never in Git before |
| 22 | OpenTelemetry Operator (new, `opentelemetry-operator-system` namespace) | `otel-operator.yaml` |
| 23 | `Instrumentation` CR + 2 pilot service annotations (`system-health-service`, `utm-sim`) | `otel-instrumentation.yaml` + `patches/*.json` |

### Objects outside `argocd-ns/utm-statistics`

These live in other namespaces and must follow whichever application manages
each one. Item 14 is the sharpest risk in this table: it is a change to an
**existing** object, so a resync reverts it without anything being deleted.

| # | Object | Namespace | Effect if reverted |
|---|---|---|---|
| 12 | `servicemonitor/rook-ceph-mgr` | `rook-ceph` | dashboard 04 goes blank |
| 13 | `servicemonitor/ingress-nginx-controller` + `svc/ingress-nginx-metrics` | `ingress-nginx` | the only RED metrics on the platform are lost |
| 14 | **`deploy/keycloak` env** — `KC_METRICS_ENABLED`, `KC_HEALTH_ENABLED` | `utm-core` | `/keycloak/auth/metrics` returns 404 again; dashboard 11's three runtime rows go to NO DATA |
| 15 | `servicemonitor/keycloak` + `svc/keycloak-metrics` | `utm-core` | same as 14, from the scrape side |
| 16 | `prometheusrule/utm-observability` | `monitoring` | all 32 alerts disappear |
| 17 | OTel Operator (Deployment, CRDs, RBAC, webhooks, Certificate/Issuer) | `opentelemetry-operator-system` (new namespace) | no auto-instrumentation injection cluster-wide; pods fall back to running unmutated (webhook `failurePolicy: Ignore` on pod mutation) |
| 18 | `Instrumentation/utm-app-tracing` + pilot pod annotations | `utm-services` | the 2 pilot services (and any future ones) stop getting traced; silent, not an error |
| 19 | `Instrumentation/utm-app-tracing` (3 more, one per namespace) + 32 fan-out pod annotations | `utm-skyzr`, `utm-core`, `utm-aviamaps` (CRs) + all 4 (pod annotations) | all 34 instrumented services (not just the 2 pilots) silently stop getting traced on resync |

Items 14 and 15 are alarmed by `KeycloakMetricsMissing`, which fires 15 minutes
after the `keycloak-metrics` job disappears. That is a detector, not a
safeguard — it tells you the regression happened.

### Data-plane, not GitOps-managed

The `uas-zones-geo` Elasticsearch index and the `grafana_ro` MySQL user are not
ArgoCD-managed and will survive a resync. Both are reproducible from
`uas-zones-geo-build.sh` and the `*-grant.sql` files.

The Calico v3.27.0 → v3.27.5 bump (`ds/calico-node`, `deploy/calico-kube-controllers`,
`kube-system`) is also not ArgoCD-managed — no `ownerReferences`, installed via
raw `kubectl apply`, independent of any Rancher/ArgoCD application. It will
survive a resync of `utm-statistics` or anything else, but it is **not backed
by a manifest in this repo either** — if the cluster is ever rebuilt from Git,
Calico would need to be re-applied from `calico-upgrade/apply-upgrade.yaml`
(currently only on the operator's local machine) or reinstalled fresh at
whatever version is current then.

> **Note on `allowUiUpdates`.** The observability provider sets
> `allowUiUpdates: false`, so Grafana marks those dashboards read-only in the
> UI. This is deliberate: with it true, UI edits are silently overwritten on the
> next provisioning sweep. Changes go through Git.

---

## 12. Runbooks

### 12.1 Incident triage path

```
00 Executive Overview   ── is anything wrong at all?
        ↓
01 Platform Health      ── which namespace / pod? restart leaderboard
        ↓
02 Kubernetes Workloads ── which container? waiting + termination reasons
        ↓
03 Nodes & Resources    ── is the node starved? disk / CPU / memory
        ↓
04 Ceph & Storage       ── is storage the root cause? slow ops, PGs, PVCs
        ↓
08 Coverage             ── can I even see this, or am I blind here?
```

The final step is deliberate. When a signal is missing, dashboard 08 tells you
whether that means *healthy* or *unmonitored*.

### 12.2 "Pods are restarting across the cluster"

1. **01 → Pod Restart Leaderboard.** Check the **1h rate** column, not just the
   total. A high total with a zero rate is historical.
2. **02 → Last Termination Reasons.** `OOMKilled` → memory limit. `Error` →
   application.
3. **03 → Node Disk Pressure.** Above 80% on any node, go to step 4.
4. **04 → Slow Ops + OSD latency.** Rising latency confirms the storage cascade.
5. If storage is the cause, the application pods are **victims, not culprits**.

### 12.3 "A dashboard shows no data"

1. Does the panel say **NO DATA** or show a value of 0? They are different.
2. Check the dashboard time range against §3.2 — datasets 05–07 are historical.
3. **08 → Prometheus Coverage** → is the datasource reachable, are targets up?
4. Check the gap register (§9) — the capability may not exist at all.

### 12.4 Reopening the closed Elasticsearch index

```bash
export KUBECONFIG=uas.yaml
ES="kubectl -n utm-statistics exec utm-statistics-es7-0 -c utm-statistics-es7 --"

$ES curl -s -XPOST 'http://localhost:9200/utm-op-statistics-000001/_open'
```

Reopening restores the **broken-mapping** index to the wildcard and will
reintroduce the shard-failure under-reporting. Only do this to recover the
original documents; `utm-op-statistics-000001-v2` already holds them correctly
mapped.

### 12.5 Verifying Ceph metrics are still flowing

```bash
kubectl -n monitoring exec prometheus-kube-prometheus-stack-prometheus-0 \
  -c prometheus -- sh -c \
  "wget -qO- 'http://localhost:9090/api/v1/query?query=ceph_health_status'"
```

Empty result → the `rook-ceph-mgr` ServiceMonitor has been removed or lost its
`release: kube-prometheus-stack` label. `UTMCephMetricsMissing` also alerts on this.

---

## 13. Assumptions

1. **UTM namespace regex** — `utm-core|utm-services|utm-skyzr|utm-aviamaps|utm-statistics|utm-devops`, which yields the 69 pods cited throughout.
2. **Environment label** — not hard-coded to "Production"; no reliable environment marker exists in the cluster, so it is omitted rather than guessed.
3. **Disk thresholds** — 70 / 80 / 90 %, set per panel and adjustable in field config.
4. **Root filesystem selector** — `mountpoint="/", fstype!="rootfs"`, the standard node-exporter idiom.
5. **KPI single-bucket aggregation** — Grafana's ES datasource rejects a metric query with no bucket agg, so KPI stats use a 1-year `date_histogram` with `min_doc_count: 1`. **Caveat:** if a dataset spans two calendar years within the visible range, these tiles show only the later year.
6. **41 vs 37 plans** — treated as normal lag, **not** a pipeline failure. The two stores capture different lifecycle stages (live has a full spread; the statistics copy is entirely CLOSED), so a difference is expected by design.
7. **`operation-plan-result` time field** — `operationPlanResult.startTime`; the index has no document-level created/updated timestamp.

---

## 14. Appendix A — file manifest

```
LFV/
├── observability/
│   ├── 00-executive-overview.json          30 panels
│   ├── 01-platform-health.json              9 panels
│   ├── 02-kubernetes-workloads.json        13 panels
│   ├── 03-nodes-cluster.json                9 panels
│   ├── 04-ceph-storage.json                22 panels
│   ├── 05-uas-zones.json                   14 panels
│   ├── 06-operation-plans.json             17 panels
│   ├── 07-user-activity.json               12 panels
│   ├── 08-observability-coverage.json      21 panels
│   ├── 09-drone-plan-statistics.json       17 panels
│   ├── 10-dor-registry.json
│   ├── 11-keycloak-identity.json
│   ├── 12-application-logs.json            Logs panel + namespace variable (NEW 2026-10-02)
│   ├── loki-config.yaml                    Loki: ConfigMap + PVC + Deployment + Service (NEW)
│   ├── alloy-logs.yaml                     Alloy DaemonSet + RBAC (NEW)
│   ├── tempo-config.yaml                   Tempo ConfigMap, +metrics_generator (NEW, first
│   │                                        time this pre-existing object is in Git at all)
│   ├── otel-operator.yaml                  OTel Operator v0.160.0 (NEW), pilot phase of the
│   │                                        "trace all utm-* apps" initiative
│   ├── otel-instrumentation.yaml           Instrumentation CR (utm-services), Tempo OTLP :4318 (NEW)
│   ├── otel-instrumentation-core.yaml      Instrumentation CR (utm-core) (NEW)
│   ├── otel-instrumentation-skyzr.yaml     Instrumentation CR (utm-skyzr) (NEW)
│   ├── otel-instrumentation-aviamaps.yaml  Instrumentation CR (utm-aviamaps) (NEW)
│   ├── instrument-all.ps1                  fan-out script: 4 CRs + 34 patches + rollout (NEW)
│   ├── patches/*.json                      34 kubectl --patch-file injection annotations (NEW)
│   ├── datasource.yaml                     12 datasources (+Loki; Jaeger/Tempo repointed)
│   ├── provider.yaml                       2 providers
│   ├── configmap-dashboards.yaml           dashboards ConfigMap
│   ├── deployment-patch.json               volume + mount
│   ├── servicemonitor-rook-ceph-mgr.yaml   enables Ceph metrics
│   ├── alert-rules.yaml                    32 PrometheusRule alerts, 6 groups
│   └── README.md                           technical reference
├── docs/
│   ├── UTM-Observability-Documentation.md       this document
│   └── UTM-Observability-Documentation.confluence.xml
├── drone-plan-dashboard-enhanced.json      17 panels
├── es-create-index.json                    corrected ES mapping
├── es-reindex.json                         reindex body
├── grafana-paths-patch.json                provisioning path fix
└── grafana-pvc-patch.json                  PVC + chown fix
```

---

## 15. Appendix B — reference queries

### 15.1 PromQL

```promql
# Node root-filesystem utilisation (the cascade's head)
(node_filesystem_size_bytes{mountpoint="/",fstype!="rootfs"}
 - node_filesystem_avail_bytes{mountpoint="/",fstype!="rootfs"})
/ node_filesystem_size_bytes{mountpoint="/",fstype!="rootfs"} * 100

# Active crash loops (rate, not the cumulative counter)
sum by (namespace,pod) (
  rate(kube_pod_container_status_restarts_total{namespace=~"utm-.*"}[5m]))

# Degraded deployments
kube_deployment_status_replicas_available{namespace=~"utm-.*"}
  < kube_deployment_status_replicas{namespace=~"utm-.*"}

# Ceph slow ops — the storage→Kubernetes cascade signal
sum(ceph_healthcheck_slow_ops)

# PVC utilisation
kubelet_volume_stats_used_bytes / kubelet_volume_stats_capacity_bytes * 100

# Endpoint coverage
count(probe_success) / count(kube_ingress_info{namespace=~"utm-.*"}) * 100
```

### 15.2 Elasticsearch

```bash
# ALWAYS use _count, never _cat/indices docs.count
GET uas_9.0.0/_count
GET operation-plan-wrapper_3.2.2-p2/_count

# Confirm a field is aggregatable before building a panel on it
GET ops-subscription_3.2.2-p2/_field_caps?fields=state,subscriptionType

# Detect the silent partial-result failure — check _shards, not just hits
POST utm-op-statistics*/_search?size=0
{ "track_total_hits": true,
  "aggs": {"state": {"terms": {"field": "state"}}} }
# → inspect _shards.failed; a non-zero value means results are INCOMPLETE
```

### 15.3 Useful kubectl

```bash
export KUBECONFIG=uas.yaml

# Top restarting pods cluster-wide
kubectl get pods -A --no-headers \
  | awk '{print $5, $1"/"$2, $4}' | sort -rn | head -15

# Pods Running but not Ready (invisible to a normal scan)
kubectl get pods -A --no-headers | awk '$3=="Running"' \
  | awk -F'[ /]+' '$3!=$4 {print $1"/"$2, $3"/"$4}'

# Ceph health from the toolbox
kubectl -n rook-ceph exec deploy/rook-ceph-tools -- ceph status
kubectl -n rook-ceph exec deploy/rook-ceph-tools -- ceph health detail
```

---

*End of document.*
