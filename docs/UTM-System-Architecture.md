# UTM System Architecture

**Scope:** what this platform does, how its pieces fit together, and what each
service is for. Investigated 2026-10-02 against the live cluster.

**Confidence discipline:** every claim below is tagged with its evidence
source. `[CONFIRMED: X]` means directly observed (config, schema, live API,
trace, or source code). `[INFERRED]` means a reasonable reading of the name/
image/context, not independently verified — treat these as a starting
hypothesis, not fact. Nothing here is guessed without being labeled as such.

This cluster was mid-recovery from the 2026-10-02 resource-cascade incident
(see `UTM-Observability-Documentation.md`, gap G4) during this investigation.
Elasticsearch was unreachable for parts of it; sections relying on it cite the
main doc's earlier-captured data instead of re-querying.

---

## 1. Executive summary

This is a **UAS Traffic Management (UTM)** platform — it lets drone operators
register, get identity-verified, plan flights, get those flights checked
against airspace restrictions and other traffic, and fly them, while feeding
telemetry and compliance data back to the platform. It's built for at least
two national markets with different identity/regulatory requirements
(Sweden and Austria — see §4.3), sharing most of the same backend logic.

Three largely independent product surfaces share this cluster:
1. **FLYK** (`utm-aviamaps`) — the pilot-facing flight-planning web app
2. **DOR** (`utm-skyzr`) — Drone Operator Registry: operator registration,
   organisation management, identity verification (two country variants)
3. **UTM core services** (`utm-services`) — the shared backend: airspace
   data, geozone evaluation, operation-plan lifecycle, conflict detection,
   telemetry ingestion

`utm-core` provides shared platform infrastructure (two Elasticsearch
clusters' worth of data stores, one MariaDB instance, one message broker, two
Keycloak identity providers) that the other namespaces depend on.

---

## 2. Namespace / subsystem map

| Namespace | Role | Evidence |
|---|---|---|
| `utm-core` | Shared platform infra: Elasticsearch (`core-es7`), MariaDB (`core-database`), ActiveMQ Artemis broker, GeoServer, the "swim"-realm Keycloak (machine/service identity) | `[CONFIRMED]` — direct inspection, this session and prior |
| `utm-services` | UTM business logic: ~37 services covering airspace data, geozones, operation plans, conflict detection, telemetry, health/status | `[CONFIRMED]` namespace contents; `[INFERRED]` most individual purposes (see §3) |
| `utm-skyzr` | DOR (Drone Operator Registry): operator/organisation registration, two country variants (SE via BankID, AT via ID Austria), each with its own Keycloak | `[CONFIRMED]` — DB schemas, eID config, ingress paths |
| `utm-aviamaps` | FLYK: the pilot-facing flight-planning frontend + backend, exposed via a Cloudflare tunnel | `[CONFIRMED]` — routes read from source (prior session), ingress path `/avm/` |
| `utm-devops` | Internal tooling: Vault (secrets), a `uas-tool`/`uas-web` pair (nginx-fronted) | `[INFERRED]` — not investigated deeply this pass, out of scope (no traceable business logic) |
| `utm-statistics` | Observability stack (Grafana, Loki, Tempo, a second Elasticsearch for statistics copies) — not part of UTM's business logic, built by this engagement | `[CONFIRMED]` |

---

## 3. Per-service purpose (`utm-services`)

Grouped by evidence strength. Every entry's basis is stated; none should be
read as more certain than its tag says.

### 3.1 Confirmed via live trace data, config, or source (this session)

| Service | Purpose | Evidence |
|---|---|---|
| `operation-plan-service` | Owns the operation-plan lifecycle (create/authorize/activate). Trusts only the `swim` realm with role `partner_utm` — end users never call it directly | `[CONFIRMED]` — BankID doc §8.4: 401 on a user token, `issuer-uri=.../realms/swim` |
| `dynamic-geozone-atm` | Serves/updates geozone data specifically for ATM (air traffic management) consumption — has an admin update endpoint | `[CONFIRMED]` trace: `POST /admin/geozone-for-atm/update` |
| `airspace-data-collector` | Runs a queue-driven background worker that processes queued operations one at a time | `[CONFIRMED]` trace: `TriggerQueueScheduler.processNextOperationInQueue`, fires repeatedly on a schedule |
| `cms` | Runs a scheduled status-reporting job | `[CONFIRMED]` trace: `CmsScheduler.sendStatusReport` |
| `smartsis` + `smartsis-event-bridge` | `smartsis-event-bridge` consumes the `keycloakadminevent` topic (published by a `smartsis-listener` Keycloak plugin on the `swim` realm) and forwards each event as a webhook POST to `smartsis`'s own `/smartsis/keycloakEvent` endpoint | `[CONFIRMED]` — env vars (`AMQP_TOPIC`, `SMARTSIS_WEBHOOK_URL`), cross-referenced against the BankID doc's listing of `utm-core/keycloak`'s `smartsis-listener-1.1.0.jar` provider |
| `system-health-service` | Publishes platform health updates to the `systemhealthupdated` broker address; runs its own internal `HealthCheckScheduler` | `[CONFIRMED]` — broker address list + earlier-session trace evidence (`HealthCheckScheduler.scheduledHealthCheckTask`) |
| `quartz-scheduler` | A Quartz job scheduler backed by the dedicated `quartz` MariaDB schema (`qrtz_*` tables) — a shared/central scheduling service, not just this one pod's internal timers | `[CONFIRMED]` — DB schema inspection |
| `cars` | Validates against Keycloak at startup; written in Rust (not Java like the rest of the fleet) | `[CONFIRMED]` — panic log: `"An error has occurred while preparing keycloak validation config"`; binary is `./cars`, not `java -jar` |

### 3.2 Inferred from naming, image, and domain convention — not independently verified

| Service | Likely purpose | Basis |
|---|---|---|
| `aixm` | Processes AIXM (Aeronautical Information Exchange Model) data — the standard format for airspace/aeronautical data | `[INFERRED]` — AIXM is a real, standard aviation data format; `uas_9.0.0`'s `dataSource.originator` field includes the literal value `AIXM-import` (confirmed in the observability doc), which supports this being the import pipeline |
| `aixm-geozone-adapter` | Converts AIXM data into the platform's internal geozone representation | `[INFERRED]` from name; has its own ingress (`/aixm-geozone-adapter/`), so it's externally callable, not purely internal |
| `notam-geozone-adapter` | Same pattern as above, for NOTAM (Notice to Airmen) data instead of AIXM | `[INFERRED]` from name only |
| `geozone-condition-language` | Evaluates a rules/condition language against geozones (e.g. time-of-day or altitude-based restrictions) | `[INFERRED]` from name; internal-only (no ingress) |
| `conflict-detection` | Deconfliction — checks whether a proposed operation plan conflicts with existing authorized operations | `[INFERRED]` from name, consistent with standard U-space deconfliction services; internal-only |
| `lara-adapter` | LARA is a real, known term in European U-space regulation (local airspace reservation/authorization service) — likely bridges to an external or regional LARA system | `[INFERRED]` — name matches a real regulatory term (also referenced in the observability doc's `laraAirspaceProperties.reservations` nested ES field), but the actual integration target was not identified this session |
| `environment-manager` | Manages environment/configuration data; publishes to `environment.config.update` | `[CONFIRMED]` broker address exists; `[INFERRED]` that this service owns it specifically — not directly proven which service publishes vs. consumes it |
| `telemetry-manager` | Manages drone telemetry data; writes to `core-es7` (confirmed in observability doc §3.1) | `[CONFIRMED]` writes to core-es7; `[INFERRED]` relationship to the `heartbeat-telemetry`/`pose2` broker addresses (plausible, not proven — `drone-flight-service` is an equally plausible publisher) |
| `drone-flight-service` | Manages active flight sessions; plausible publisher of `pose2` (position) and `heartbeat-telemetry` | `[INFERRED]` from name only — not confirmed against either broker address's actual publisher |
| `alerting-manager` | Generates/manages platform alerts — likely the producer behind `utm-alerting-statistics` (confirmed empty in the observability doc, consistent with either this service never having fired, or writing elsewhere) | `[INFERRED]` |
| `altitude-conversion` | A focused utility converting between altitude reference systems (AMSL/AGL/flight level) — a common, narrow need in aviation data pipelines | `[INFERRED]` from name; `java -cp` launch with an explicit main class (`MainElevationConverter`) supports "narrow utility" reading |
| `es-migration` | A one-shot/periodic Elasticsearch migration or reindexing job | `[INFERRED]` from name; confirmed in observability doc to write to core-es7 |
| `operation-plan-report-service` | Generates reports from operation-plan data; built on Quarkus (not Spring Boot, unlike most of the fleet) | `[CONFIRMED]` Quarkus runtime (`quarkus-run.jar`); `[INFERRED]` purpose from name |
| `ras-manager` | Unclear. "RAS" has no confirmed expansion in this system | `[INFERRED — LOW CONFIDENCE]`, name only |
| `cme-manager` | Unclear. Possibly "Conflict/Collision Management Engine" or similar, consistent with sitting alongside `conflict-detection` | `[INFERRED — LOW CONFIDENCE]`, name only |
| `utm-manager` | Appears to be a general orchestration/management service for the platform, given the generic name and that it's one of the larger JVM heap allocations (3G) | `[INFERRED — LOW CONFIDENCE]` |
| `service-registry` | A `.war`-packaged service; given the name and era, likely a Eureka-style service registry/discovery server (legacy pattern — most other services don't appear to register with it dynamically) | `[INFERRED]` — not confirmed as Eureka specifically; its actuator/API was unreachable during this investigation |
| `user-portal` (+`-client`) | A general platform user portal, separate from DOR's own portal (`dms-fe`/`/pm/`) — possibly for `swim`-realm (core) users rather than DOR operators | `[INFERRED]` — the distinction from DOR's portal is real (separate ingress paths, separate services) but the actual audience/purpose split was not confirmed |
| `utm-geotool`, `utm-sim` | Node.js services; `utm-sim` ("simulator") plausibly generates synthetic test traffic; `utm-geotool` plausibly a geospatial utility | `[INFERRED — LOW CONFIDENCE]`, names only, no ingress for either (internal-only) |
| `geoserver-layer-catalog` | Manages/catalogs layers for the `utm-core/geoserver` GIS server | `[INFERRED]` from name |
| `keycloak-migration` | A one-shot Keycloak data/config migration job | `[INFERRED]` from name |
| `message-logger` (+`-client`,`-es7`) | Logs messages (likely integration/audit messages) to its own dedicated Elasticsearch 7.4 instance; has both a backend and a frontend client | `[INFERRED]` from name and topology; the ES7.4 instance is notably a different, older ES version than `core-es7`'s 7.17.18 |
| `service-registry-es5`, `service-registry-mysql` | A legacy Elasticsearch 5 + MySQL 5.7 pair backing `service-registry` — explicitly called out as a legacy stack in the observability doc | `[CONFIRMED legacy]` per existing doc; purpose within service-registry not further investigated |

Two broker addresses (`pose2`, `pullingtask`) remain **unattributed** — a
real address exists on the broker, but no specific publisher/consumer was
confirmed this session. `[OPEN — not verified]`.

---

## 4. Data flow

### 4.1 Identity — fully documented elsewhere, not re-investigated

The BankID → Keycloak → DOR registration flow, the three "doors" for
creating an operator, and the flight-plan credential-brokering flow (FLYK
using its own service-account token, never the end user's) are already
documented in detail and verified end-to-end in
`BankID-Keycloak-SKYZR-Integration.md` §8. Not duplicated here — see that
document directly. Key fact worth repeating because it shapes everything
else: **the end user's own DOR token never reaches `operation-plan-service`**;
FLYK brokers with a `swim`-realm service credential instead.

### 4.2 Operation-plan lifecycle (partially confirmed)

```
FLYK (pilot-facing)
   │ swim-realm service credential (client_credentials)
   ▼
operation-plan-service          — owns plan create/authorize/activate
   │ [INFERRED: publishes to the "operationplanwrapper" broker address —
   │  the address name matches the operation-plan-wrapper ES index, and a
   │  live queue was observed bound to it, but the actual publisher was
   │  not traced]
   ▼
core-es7: operation-plan-wrapper*, operation-plan-result*
```

The existing observability doc already found a real data-quality signal
here worth repeating: **`operation-plan-result` shows `TIMEOUT` on 7 of 24
(29%)** of authorization/activation attempts — a genuine operational issue,
not an artifact of this investigation.

`[CONFIRMED]` separately: `conflict-detection`, `dynamic-geozone-atm`,
`geozone-condition-language` all sit in `utm-services` with no ingress
(internal-only) and plausible names for a deconfliction/geozone-check step
somewhere in this pipeline — but no trace evidence yet confirms their exact
position in the sequence. `[OPEN — not verified]`.

### 4.3 DOR is multi-tenant: at least two country deployments

This was the most significant new finding this session. `utm-skyzr` runs
**two parallel DOR stacks**, sharing the same MariaDB instance but using
different schemas and different national identity systems:

| | Sweden (`dor-be`) | Austria (`dor-acg`) |
|---|---|---|
| DB schema | `dor_be` | `dms` (same table structure, plus `payments`, `registration_certificates`, `ac_legacy_registrations`) |
| Identity | BankID, via `dor-keycloak` (Keycloak 26.7.3) | Austrian eID ("ID Austria"), via `dor-acg-keycloak` (Keycloak 17.0.1) — `MOA_BASE_URL=https://eid2.oesterreich.gv.at/auth/idp/profile/oidc` |
| Frontend path | `/pm/` | `/pm-acg/` |
| Payments | not observed | `PAYMENT_SUCCESS_URL`/`FAIL`/`ABORT` all present — Austria's flow includes a payment step Sweden's doesn't |

`[CONFIRMED]` via direct ConfigMap inspection (`dor-be` and `dor-acg`
ConfigMaps) and live DB schema comparison. The `dms` schema is very likely
either an older/legacy name for the same concept predating the `dor_be`
split, or ACG's deployment simply kept the original "DMS" (Drone Management
System) naming while the Swedish side was renamed to `dor_be` — which of
these is correct is `[OPEN — not verified]`, but the two-country,
two-identity-provider, two-schema split itself is solid.

### 4.4 Messaging backbone (ActiveMQ Artemis, `utm-core/activemq`)

Live broker addresses, queried directly via the Artemis management API —
the first authoritative, non-inferred list of what actually flows through
this system asynchronously:

```
operationplanwrapper      — matches the operation-plan-wrapper ES index name
keycloakadminevent        — Keycloak (swim realm) admin events → smartsis-event-bridge → smartsis
systemhealthupdated       — platform health change notifications
environment.config.update — environment/config change notifications
heartbeat-telemetry        — drone telemetry heartbeats  [publisher not confirmed]
pose2                      — drone position/pose updates  [publisher not confirmed]
pullingtask                — a task-pulling/dispatch address  [purpose not confirmed]
```

Only the first three have a confirmed producer or consumer attached to them
by name; the rest are real addresses with unconfirmed participants.

### 4.5 Live trace data — too sparse for a real call graph yet

OTel auto-instrumentation was only enabled this session (see observability
doc gap G4), and the cluster has been recovering from an incident since.
14 services have *some* trace data; every trace sampled during this
investigation was **single-service** (health checks, internal scheduled
jobs) — no cross-service parent/child span was found. This is expected
given how recent and how disrupted the rollout was, not evidence that
services don't call each other. **Revisit this once the cluster and trace
data have both had time to stabilize — it's the most reliable way to
confirm the rest of this document's `[INFERRED]` claims.**

---

## 5. Data stores

| Store | Used by (confirmed) | Content |
|---|---|---|
| `core-es7` (Elasticsearch 7.17.18, `utm-core`) | `operation-plan-service`, `operation-plan-report-service` (`oprs`), `es-migration`, `telemetry-manager` | Live operational data: `uas_9.0.0` (airspace zones), `operation-plan-wrapper*`, `operation-plan-result*`, `ops-subscription` — field-level detail already in the observability doc §3.2/§8.5 |
| `utm-statistics-es7` (`utm-statistics`) | `utm-statistics-service` | Statistics copies + historical/Jaeger data, separate from live operational data — frequent source of confusion per the observability doc |
| `message-logger-es7` (ES **7.4.0**, `utm-services`) | `message-logger` | Its own dedicated, older-version ES instance — not shared with `core-es7` |
| `service-registry-es5` (ES **5**, `utm-services`) | `service-registry` | Legacy, explicitly called out as such in the observability doc |
| `core-database` (MariaDB, `utm-core`) — 11 schemas | multiple | `dor_be` + `dms` (DOR business data, two country variants — see §4.3), `dor_keycloak` + `dor_acg_keycloak` + `keycloak` (three separate Keycloak schemas, one per identity provider instance), `quartz` (shared job scheduler state) |
| ActiveMQ Artemis (`utm-core`) | multiple, see §4.4 | Not a data store in the durable sense, but the async backbone connecting several of the above |

---

## 6. Open questions / explicitly not verified

Listed here so a reader doesn't mistake silence for confirmation:

1. **Most `utm-services` purposes beyond §3.1** are name-based inference,
   not confirmed. Actuator/API endpoints are closed on every service tried
   (consistent with the observability doc's existing "0 of 84 services
   instrumented" finding) — there was no live API surface to query directly
   for most of them.
2. **`ras-manager` and `cme-manager`** — no confident purpose could be
   derived at all; their names don't map to standard U-space terminology
   the way `conflict-detection` or `lara-adapter` do.
3. **Publisher/consumer pairs for `heartbeat-telemetry`, `pose2`,
   `pullingtask`, `environment.config.update`, `operationplanwrapper`** —
   addresses confirmed to exist on the broker; which services actually
   produce/consume each was not traced.
4. **Why `dms` and `dor_be` have near-identical schemas** — confirmed as
   fact, not confirmed as to *why* (legacy rename vs. deliberate per-country
   naming).
5. **`service-registry`'s actual technology** — inferred to be Eureka-style
   from context, not confirmed; its management API was unreachable.
6. **`user-portal`'s actual audience** vs. DOR's own portal — both exist,
   serve different paths, but the intended user base for each wasn't
   confirmed.
7. **Real cross-service call graph** — not yet available from tracing (see
   §4.5). This is the single highest-value follow-up: once stable, trace
   data would convert most of §3.2's `[INFERRED]` entries to `[CONFIRMED]`.

---

*Investigated and written 2026-10-02, against a cluster that was actively
recovering from an unrelated incident during parts of this work — see
`UTM-Observability-Documentation.md` gap G4 for that context. Treat any ES-
dependent fact not freshly re-queried as carried over from that document's
earlier (stable-cluster) findings.*
