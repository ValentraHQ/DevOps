"""Build dashboard 11 - Keycloak Identity, and add it to every dashboard's nav.

Data sources, all verified before any panel was written:

  keycloak.event_entity  3,480 live auth events, 2026-08-26 -> now
                         CLIENT_LOGIN 3437, LOGIN 21, CODE_TO_TOKEN 14,
                         CODE_TO_TOKEN_ERROR 8
  keycloak.realm/client/user_entity/keycloak_role   inventory
  Prometheus             pod health for utm-core/keycloak

Keycloak exposes NO Prometheus metrics here: KC_METRICS_ENABLED is unset and
/keycloak/auth/metrics returns 404. Runtime panels therefore come from
kube-state-metrics/cAdvisor, and the gap is stated on the dashboard rather
than implied.

Reads via grafana_ro over the dor-be-mysql datasource using fully-qualified
keycloak.* names (the datasource's default schema is dor_be).
"""
import glob
import json
import os

OUT = r"C:/Users/sdilag/LFV/observability"
SQL = {"type": "mysql", "uid": "dor-be-mysql"}
PROM = {"type": "prometheus", "uid": "prometheus-uid"}
TEXT, OK, WARN, HIGH, CRIT = "text", "green", "#EAB839", "orange", "red"

# Keycloak stores EVENT_TIME as epoch milliseconds.
TS = "FROM_UNIXTIME(event_time/1000)"
TFILT = f"$__timeFilter({TS})"

_id = [200]


def nid():
    _id[0] += 1
    return _id[0]


def fc(unit=None, steps=None, custom=None, color=None, dec=None):
    d = {"mappings": [],
         "thresholds": {"mode": "absolute",
                        "steps": steps or [{"color": TEXT, "value": None}]},
         "color": color or {"mode": "thresholds"},
         "noValue": "NO DATA"}
    if unit:
        d["unit"] = unit
    if dec is not None:
        d["decimals"] = dec
    if custom:
        d["custom"] = custom
    return {"defaults": d, "overrides": []}


def sq(sql, fmt="table"):
    return [{"refId": "A", "datasource": SQL, "format": fmt,
             "rawSql": sql, "rawQuery": True, "editorMode": "code"}]


def pq(expr, legend="", instant=False, fmt="time_series"):
    return [{"refId": "A", "datasource": PROM, "expr": expr,
             "legendFormat": legend, "instant": instant, "format": fmt,
             "editorMode": "code", "range": not instant}]


def stat(title, desc, gp, targets, ds, fieldcfg=None, bg=False, graph="none"):
    return {"id": nid(), "type": "stat", "title": title, "description": desc,
            "datasource": ds, "gridPos": gp, "fieldConfig": fieldcfg or fc(),
            "options": {"colorMode": "background_solid" if bg else "value",
                        "graphMode": graph, "justifyMode": "auto",
                        "orientation": "auto", "textMode": "auto",
                        "wideLayout": True, "showPercentChange": False,
                        "reduceOptions": {"calcs": ["lastNotNull"], "fields": "",
                                          "values": False}},
            "pluginVersion": "12.1.1", "targets": targets}


def ts(title, desc, gp, targets, ds, unit="short", stack=False, draw="bars",
       legend=None):
    custom = {"drawStyle": draw, "lineWidth": 0 if draw == "bars" else 2,
              "fillOpacity": 80 if draw == "bars" else 15,
              "gradientMode": "none", "showPoints": "auto", "pointSize": 5,
              "spanNulls": False, "axisPlacement": "auto", "axisLabel": "",
              "scaleDistribution": {"type": "linear"}, "barAlignment": 0,
              "insertNulls": False, "axisBorderShow": False,
              "hideFrom": {"legend": False, "tooltip": False, "viz": False},
              "stacking": {"group": "A", "mode": "normal" if stack else "none"},
              "thresholdsStyle": {"mode": "off"}}
    return {"id": nid(), "type": "timeseries", "title": title,
            "description": desc, "datasource": ds, "gridPos": gp,
            "fieldConfig": fc(unit=unit, custom=custom,
                              color={"mode": "palette-classic"}),
            "options": {"legend": {"calcs": legend or [], "displayMode": "list",
                                   "placement": "bottom", "showLegend": True},
                        "tooltip": {"mode": "multi", "sort": "desc",
                                    "hideZeros": False}},
            "pluginVersion": "12.1.1", "targets": targets}


def table(title, desc, gp, targets, ds, overrides=None, sort=None, footer=False):
    o = {"cellHeight": "sm", "showHeader": True,
         "footer": {"show": footer, "reducer": ["sum"], "countRows": False,
                    "fields": ""}}
    if sort:
        o["sortBy"] = sort
    return {"id": nid(), "type": "table", "title": title, "description": desc,
            "datasource": ds, "gridPos": gp,
            "fieldConfig": {"defaults": {"color": {"mode": "thresholds"},
                                         "mappings": [], "noValue": "NO DATA",
                                         "custom": {"align": "auto",
                                                    "cellOptions": {"type": "auto"},
                                                    "filterable": True,
                                                    "inspect": False},
                                         "thresholds": {"mode": "absolute",
                                                        "steps": [{"color": TEXT,
                                                                   "value": None}]}},
                            "overrides": overrides or []},
            "options": o, "pluginVersion": "12.1.1", "targets": targets}


def barchart(title, desc, gp, targets, ds, horizontal=True):
    return {"id": nid(), "type": "barchart", "title": title, "description": desc,
            "datasource": ds, "gridPos": gp,
            "fieldConfig": fc(unit="short", color={"mode": "palette-classic"},
                              custom={"lineWidth": 1, "fillOpacity": 80,
                                      "axisPlacement": "auto",
                                      "axisBorderShow": False,
                                      "hideFrom": {"legend": False,
                                                   "tooltip": False,
                                                   "viz": False}}),
            "options": {"orientation": "horizontal" if horizontal else "vertical",
                        "showValue": "auto", "xTickLabelRotation": 0,
                        "legend": {"displayMode": "list", "placement": "bottom",
                                   "showLegend": False},
                        "tooltip": {"mode": "single", "sort": "none",
                                    "hideZeros": False}},
            "pluginVersion": "12.1.1", "targets": targets}


def text(title, content, gp):
    return {"id": nid(), "type": "text", "title": title, "gridPos": gp,
            "options": {"mode": "markdown",
                        "code": {"language": "plaintext",
                                 "showLineNumbers": False, "showMiniMap": False},
                        "content": content},
            "pluginVersion": "12.1.1"}


def row(title, y):
    return {"id": nid(), "type": "row", "title": title, "collapsed": False,
            "gridPos": {"h": 1, "w": 24, "x": 0, "y": y}, "panels": []}


SUITE = [
    ("utm-obs-00-executive", "00 Overview"),
    ("utm-obs-01-health", "01 Health"),
    ("utm-obs-02-workloads", "02 Workloads"),
    ("utm-obs-03-nodes", "03 Nodes"),
    ("utm-obs-04-ceph", "04 Ceph"),
    ("utm-obs-05-uas", "05 UAS Zones"),
    ("utm-obs-06-opplans", "06 Op Plans"),
    ("utm-obs-07-users", "07 Users"),
    ("utm-obs-08-coverage", "08 Coverage"),
    ("utm-drone-plans", "09 Drone Plans"),
    ("utm-obs-10-dor", "10 DOR Registry"),
    ("utm-obs-11-keycloak", "11 Keycloak"),
]


def nav():
    return [{"title": t, "type": "link", "url": f"/d/{u}", "icon": "dashboard",
             "tooltip": "", "tags": [], "asDropdown": False,
             "targetBlank": False, "includeVars": True, "keepTime": True}
            for u, t in SUITE]


# ===========================================================================
p, y = [], 0
KC_NS = 'namespace="utm-core",pod=~"keycloak-.*"'

p.append(row("Identity Overview", y)); y += 1
kpis = [
    ("Auth Events", "All rows in keycloak.event_entity (all time).",
     "SELECT COUNT(*) AS value FROM keycloak.event_entity", None),
    ("Auth Failures", "Events whose type ends in _ERROR. Non-zero means clients or "
     "users are failing to authenticate.",
     "SELECT COUNT(*) AS value FROM keycloak.event_entity WHERE type LIKE '%ERROR%'",
     [{"color": TEXT, "value": None}, {"color": OK, "value": 0},
      {"color": CRIT, "value": 1}]),
    ("Authenticating Users", "COUNT(DISTINCT user_id). Opaque UUIDs only — no "
     "usernames or emails are read.",
     "SELECT COUNT(DISTINCT user_id) AS value FROM keycloak.event_entity "
     "WHERE user_id IS NOT NULL", None),
    ("Realms", "Configured realms.",
     "SELECT COUNT(*) AS value FROM keycloak.realm", None),
    ("Clients", "OIDC clients across all realms.",
     "SELECT COUNT(*) AS value FROM keycloak.client", None),
    ("Keycloak Users", "Identities in keycloak.user_entity.",
     "SELECT COUNT(*) AS value FROM keycloak.user_entity", None),
]
for i, (t, d_, s, steps) in enumerate(kpis):
    p.append(stat(t, d_, {"h": 4, "w": 4, "x": i * 4, "y": y}, sq(s), SQL,
                  fieldcfg=fc(steps=steps), bg=(t == "Auth Failures")))
y += 4

p.append(row("Authentication Activity", y)); y += 1
p.append(ts("Authentication Events Over Time",
            "Hourly buckets from keycloak.event_entity, split by event type. "
            "CLIENT_LOGIN is service-to-service (client credentials); LOGIN is an "
            "interactive user sign-in.",
            {"h": 9, "w": 16, "x": 0, "y": y},
            sq(f"SELECT FROM_UNIXTIME(FLOOR(event_time/1000/3600)*3600) AS time, "
               f"type AS metric, COUNT(*) AS value FROM keycloak.event_entity "
               f"WHERE {TFILT} GROUP BY time, type ORDER BY time", fmt="time_series"),
            SQL, stack=True, legend=["sum"]))
p.append(barchart("Events by Type",
                  "Confirmed types: CLIENT_LOGIN, LOGIN, CODE_TO_TOKEN, "
                  "CODE_TO_TOKEN_ERROR.",
                  {"h": 9, "w": 8, "x": 16, "y": y},
                  sq("SELECT type, COUNT(*) AS events FROM keycloak.event_entity "
                     "GROUP BY type ORDER BY events DESC"), SQL))
y += 9

p.append(table("Authentication by Client",
               "Which OIDC clients are authenticating, and how. A client that "
               "suddenly stops appearing here has lost its integration.",
               {"h": 8, "w": 12, "x": 0, "y": y},
               sq("SELECT e.client_id AS Client, r.name AS Realm, e.type AS `Event Type`, "
                  "COUNT(*) AS Events, "
                  "MAX(FROM_UNIXTIME(e.event_time/1000)) AS `Last Seen` "
                  "FROM keycloak.event_entity e "
                  "LEFT JOIN keycloak.realm r ON r.id = e.realm_id "
                  "GROUP BY e.client_id, r.name, e.type ORDER BY Events DESC LIMIT 50"),
               SQL, sort=[{"desc": True, "displayName": "Events"}], footer=True))
p.append(ts("Interactive Logins vs Service Logins",
            "LOGIN (human sign-in) against CLIENT_LOGIN (service accounts). "
            "Service traffic normally dominates by orders of magnitude.",
            {"h": 8, "w": 12, "x": 12, "y": y},
            sq(f"SELECT FROM_UNIXTIME(FLOOR(event_time/1000/3600)*3600) AS time, "
               f"CASE WHEN type='LOGIN' THEN 'interactive login' "
               f"WHEN type='CLIENT_LOGIN' THEN 'service login' ELSE type END AS metric, "
               f"COUNT(*) AS value FROM keycloak.event_entity WHERE {TFILT} "
               f"GROUP BY time, metric ORDER BY time", fmt="time_series"),
            SQL, legend=["sum"]))
y += 8

p.append(row("Authentication Failures", y)); y += 1
p.append(ts("Failures Over Time",
            "Any event type containing _ERROR. A spike here is an authentication "
            "outage — this is the panel that would have surfaced the Grafana OAuth "
            "failure on 2026-09-21 without reading pod logs.",
            {"h": 8, "w": 12, "x": 0, "y": y},
            sq(f"SELECT FROM_UNIXTIME(FLOOR(event_time/1000/3600)*3600) AS time, "
               f"CONCAT(type, ' / ', IFNULL(NULLIF(error,''),'no detail')) AS metric, "
               f"COUNT(*) AS value FROM keycloak.event_entity "
               f"WHERE type LIKE '%ERROR%' AND {TFILT} "
               f"GROUP BY time, metric ORDER BY time", fmt="time_series"),
            SQL, legend=["sum"]))
p.append(table("Failure Detail",
               "Grouped by client and error reason. `invalid_client_credentials` "
               "means a wrong or missing client secret; `invalid_grant` means bad "
               "user credentials.",
               {"h": 8, "w": 12, "x": 12, "y": y},
               sq("SELECT e.client_id AS Client, e.type AS `Event Type`, "
                  "IFNULL(NULLIF(e.error,''),'(none)') AS Error, COUNT(*) AS Count, "
                  "MIN(FROM_UNIXTIME(e.event_time/1000)) AS `First Seen`, "
                  "MAX(FROM_UNIXTIME(e.event_time/1000)) AS `Last Seen` "
                  "FROM keycloak.event_entity e WHERE e.type LIKE '%ERROR%' "
                  "GROUP BY e.client_id, e.type, Error ORDER BY Count DESC LIMIT 50"),
               SQL, sort=[{"desc": True, "displayName": "Count"}],
               overrides=[{"matcher": {"id": "byName", "options": "Count"},
                           "properties": [
                               {"id": "custom.cellOptions",
                                "value": {"type": "color-background",
                                          "mode": "gradient"}},
                               {"id": "thresholds",
                                "value": {"mode": "absolute",
                                          "steps": [{"color": OK, "value": None},
                                                    {"color": WARN, "value": 1},
                                                    {"color": CRIT, "value": 10}]}}]}]))
y += 8

p.append(row("Realm & Client Inventory", y)); y += 1
p.append(table("Realms",
               "Per-realm identity and client counts, straight from the Keycloak "
               "schema.",
               {"h": 8, "w": 12, "x": 0, "y": y},
               sq("SELECT r.name AS Realm, IF(r.enabled=1,'enabled','DISABLED') AS Status, "
                  "(SELECT COUNT(*) FROM keycloak.user_entity u WHERE u.realm_id=r.id) AS Users, "
                  "(SELECT COUNT(*) FROM keycloak.client c WHERE c.realm_id=r.id) AS Clients, "
                  "(SELECT COUNT(*) FROM keycloak.keycloak_role k WHERE k.realm_id=r.id) AS Roles "
                  "FROM keycloak.realm r ORDER BY Users DESC"),
               SQL, sort=[{"desc": True, "displayName": "Users"}]))
p.append(table("Clients",
               "OIDC clients by realm. `service account` clients authenticate as "
               "themselves (CLIENT_LOGIN); `public` clients are browser-facing.",
               {"h": 8, "w": 12, "x": 12, "y": y},
               sq("SELECT c.client_id AS Client, r.name AS Realm, "
                  "IF(c.enabled=1,'enabled','DISABLED') AS Status, "
                  "IF(c.public_client=1,'public','confidential') AS Type, "
                  "IF(c.service_accounts_enabled=1,'yes','no') AS `Service Account` "
                  "FROM keycloak.client c LEFT JOIN keycloak.realm r ON r.id=c.realm_id "
                  "ORDER BY r.name, c.client_id LIMIT 100"), SQL))
y += 8

p.append(row("Keycloak Service Health", y)); y += 1
p.append(stat("Pod Ready",
              "kube_pod_status_ready for utm-core/keycloak. 0 means the identity "
              "provider is down and nothing on the platform can authenticate.",
              {"h": 5, "w": 5, "x": 0, "y": y},
              pq(f'sum(kube_pod_status_ready{{condition="true",{KC_NS}}}) or vector(0)',
                 instant=True), PROM,
              fieldcfg=fc(steps=[{"color": CRIT, "value": None},
                                 {"color": OK, "value": 1}]), bg=True))
p.append(stat("Restarts (24h)",
              "Container restarts in the last day.",
              {"h": 5, "w": 5, "x": 5, "y": y},
              pq(f'sum(increase(kube_pod_container_status_restarts_total{{{KC_NS}}}[24h])) '
                 f'or vector(0)', instant=True), PROM,
              fieldcfg=fc(steps=[{"color": TEXT, "value": None},
                                 {"color": OK, "value": 0},
                                 {"color": WARN, "value": 1},
                                 {"color": CRIT, "value": 5}]), bg=True))
p.append(ts("Keycloak CPU", "Container CPU cores, 5m rate.",
            {"h": 5, "w": 7, "x": 10, "y": y},
            pq(f'sum(rate(container_cpu_usage_seconds_total{{{KC_NS},container!=""}}[5m]))',
               "cpu cores"), PROM, draw="line", legend=["mean", "max"]))
p.append(ts("Keycloak Memory", "Working-set memory — what the OOM killer acts on.",
            {"h": 5, "w": 7, "x": 17, "y": y},
            pq(f'sum(container_memory_working_set_bytes{{{KC_NS},container!=""}})',
               "working set"), PROM, unit="bytes", draw="line",
            legend=["mean", "max"]))
y += 5

p.append(text(
    "Scope, freshness and a known gap",
    "### What this reads\n\n"
    "`keycloak.event_entity` on `core-database` — the **live** event log of the core "
    "Keycloak (`utm-core/keycloak`, Keycloak **23.0.5**, served at "
    "`/keycloak/auth`). Verified at build time: **3,480 events**, "
    "2026-08-26 → now, all in the `swim` realm.\n\n"
    "### Data protection\n\n"
    "`grafana_ro` holds SELECT on counting columns only. Deliberately **not** "
    "granted, so unreachable even from Explore: `IP_ADDRESS`, `DETAILS_JSON`, "
    "`DETAILS_JSON_LONG_VALUE`, `SESSION_ID`, and every username/email column on "
    "`user_entity`. `USER_ID` is an opaque UUID used only for "
    "`COUNT(DISTINCT ...)` and is never displayed.\n\n"
    "### ⚠️ Known gap — no Keycloak runtime metrics\n\n"
    "`KC_METRICS_ENABLED` is unset and `/keycloak/auth/metrics` returns **404**, so "
    "Keycloak publishes no Prometheus metrics: no login latency, no token issuance "
    "rate, no session gauges, no JVM/GC detail. The *Keycloak Service Health* row "
    "above is container-level only (kube-state-metrics + cAdvisor).\n\n"
    "Closing it means setting `KC_METRICS_ENABLED=true` and **restarting the core "
    "identity provider**, which briefly interrupts authentication for the entire "
    "platform. That is a maintenance-window change, not a routine one.\n\n"
    "### Event-log retention\n\n"
    "Keycloak expires events per realm (`realm.events_expiration`). If that is set, "
    "this dashboard's history is bounded by it — a flat older region means "
    "expiry, not inactivity.",
    {"h": 9, "w": 24, "x": 0, "y": y}))

dash = {
    "uid": "utm-obs-11-keycloak", "title": "11 - Keycloak Identity",
    "description":
        "**Purpose:** authentication and identity observability for the core "
        "Keycloak (`utm-core/keycloak`) — login activity, failures, realm and "
        "client inventory, and service health.\n\n"
        "**Data sources:** `keycloak.event_entity` and the Keycloak schema via the "
        "read-only `grafana_ro` account; Prometheus for pod health.\n\n"
        "**Privacy:** no usernames, emails or IP addresses are queried. Only "
        "opaque user UUIDs, for distinct counts.\n\n"
        "**Known gap:** Keycloak exposes no Prometheus metrics here "
        "(`KC_METRICS_ENABLED` unset, `/metrics` → 404), so runtime panels are "
        "container-level only. See the note at the foot of the dashboard.\n\n"
        "**Note:** this queries a live transactional database. All panels "
        "aggregate; refresh is 5m by design.",
    "tags": ["utm-observability", "keycloak", "identity"],
    "timezone": "browser", "editable": True, "graphTooltip": 1,
    "schemaVersion": 41, "version": 0, "weekStart": "",
    "refresh": "5m", "preload": False,
    "time": {"from": "now-30d", "to": "now"},
    "timepicker": {"refresh_intervals": ["30s", "1m", "5m", "15m", "30m", "1h"],
                   "time_options": ["15m", "1h", "6h", "24h", "7d", "30d"]},
    "fiscalYearStartMonth": 0, "links": nav(),
    "templating": {"list": []},
    "annotations": {"list": [{"builtIn": 1, "type": "dashboard",
                              "datasource": {"type": "grafana",
                                             "uid": "-- Grafana --"},
                              "enable": True, "hide": True,
                              "iconColor": "rgba(0, 211, 255, 1)",
                              "name": "Annotations & Alerts"}]},
    "panels": p,
}

json.dump(dash, open(f"{OUT}/11-keycloak-identity.json", "w", encoding="utf-8"),
          indent=2)
print(f"  11-keycloak-identity.json: {len(p)} panels")

# --- refresh nav on every existing dashboard --------------------------------
n = 0
for f in sorted(glob.glob(f"{OUT}/[01]*.json")):
    if "configmap" in f or "patch" in f:
        continue
    d = json.load(open(f, encoding="utf-8"))
    if d.get("uid") == "utm-obs-11-keycloak":
        continue
    d["links"] = nav()
    json.dump(d, open(f, "w", encoding="utf-8"), indent=2)
    n += 1
print(f"  nav updated on {n} existing dashboards (12 links each)")
