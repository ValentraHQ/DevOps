# Fans out OTel auto-instrumentation to all eligible utm-* workloads, phase
# 3 of the "trace all applications" initiative (phase 1: operator, phase 2:
# 2-service pilot, both already verified -- see docs/UTM-Observability-
# Documentation.md G4 for the full writeup).
#
# Run from anywhere; uses absolute paths throughout.

$ErrorActionPreference = "Stop"
$obs = "C:\Users\sdilag\LFV\observability"

Write-Host "`n=== 1. Apply Instrumentation CRs (3 new + re-apply utm-services with the metrics/logs fix) ===" -ForegroundColor Cyan
kubectl apply -f "$obs\otel-instrumentation.yaml"
kubectl apply -f "$obs\otel-instrumentation-core.yaml"
kubectl apply -f "$obs\otel-instrumentation-skyzr.yaml"
kubectl apply -f "$obs\otel-instrumentation-aviamaps.yaml"

Write-Host "`n=== 2. Patch all 34 Deployments ===" -ForegroundColor Cyan
$targets = @(
    @{ns="utm-services"; name="airspace-data-collector"; file="airspace-data-collector-inject-java.json"},
    @{ns="utm-services"; name="aixm"; file="aixm-inject-java.json"},
    @{ns="utm-services"; name="aixm-geozone-adapter"; file="aixm-geozone-adapter-inject-java.json"},
    @{ns="utm-services"; name="alerting-manager"; file="alerting-manager-inject-java.json"},
    @{ns="utm-services"; name="altitude-conversion"; file="altitude-conversion-inject-java.json"},
    @{ns="utm-services"; name="cme-manager"; file="cme-manager-inject-java.json"},
    @{ns="utm-services"; name="cms"; file="cms-inject-java.json"},
    @{ns="utm-services"; name="conflict-detection"; file="conflict-detection-inject-java.json"},
    @{ns="utm-services"; name="drone-flight-service"; file="drone-flight-service-inject-java.json"},
    @{ns="utm-services"; name="dynamic-geozone-atm"; file="dynamic-geozone-atm-inject-java.json"},
    @{ns="utm-services"; name="environment-manager"; file="environment-manager-inject-java.json"},
    @{ns="utm-services"; name="es-migration"; file="es-migration-inject-java.json"},
    @{ns="utm-services"; name="geoserver-layer-catalog"; file="geoserver-layer-catalog-inject-java.json"},
    @{ns="utm-services"; name="geozone-condition-language"; file="geozone-condition-language-inject-java.json"},
    @{ns="utm-services"; name="keycloak-migration"; file="keycloak-migration-inject-java.json"},
    @{ns="utm-services"; name="lara-adapter"; file="lara-adapter-inject-java.json"},
    @{ns="utm-services"; name="message-logger"; file="message-logger-inject-java.json"},
    @{ns="utm-services"; name="notam-geozone-adapter"; file="notam-geozone-adapter-inject-java.json"},
    @{ns="utm-services"; name="operation-plan-report-service"; file="operation-plan-report-service-inject-java.json"},
    @{ns="utm-services"; name="operation-plan-service"; file="operation-plan-service-inject-java.json"},
    @{ns="utm-services"; name="quartz-scheduler"; file="quartz-scheduler-inject-java.json"},
    @{ns="utm-services"; name="ras-manager"; file="ras-manager-inject-java.json"},
    @{ns="utm-services"; name="service-registry"; file="service-registry-inject-java.json"},
    @{ns="utm-services"; name="smartsis"; file="smartsis-inject-java.json"},
    @{ns="utm-services"; name="smartsis-event-bridge"; file="smartsis-event-bridge-inject-java.json"},
    @{ns="utm-services"; name="telemetry-manager"; file="telemetry-manager-inject-java.json"},
    @{ns="utm-services"; name="user-portal"; file="user-portal-inject-java.json"},
    @{ns="utm-services"; name="utm-manager"; file="utm-manager-inject-java.json"},
    @{ns="utm-services"; name="utm-geotool"; file="utm-geotool-inject-nodejs.json"},
    @{ns="utm-skyzr"; name="dor-acg"; file="dor-acg-inject-java.json"},
    @{ns="utm-skyzr"; name="dor-be"; file="dor-be-inject-java.json"},
    @{ns="utm-skyzr"; name="dor-data-importer"; file="dor-data-importer-inject-java.json"},
    @{ns="utm-core"; name="geoserver"; file="geoserver-inject-java.json"},
    @{ns="utm-aviamaps"; name="flyk"; file="flyk-inject-nodejs.json"}
)

$failed = @()
foreach ($t in $targets) {
    $patchPath = "$obs\patches\$($t.file)"
    Write-Host "  patching $($t.ns)/$($t.name) ..." -NoNewline
    try {
        kubectl -n $t.ns patch deployment $t.name --patch-file="$patchPath" 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "kubectl exited $LASTEXITCODE" }
        Write-Host " ok" -ForegroundColor Green
    } catch {
        Write-Host " FAILED: $_" -ForegroundColor Red
        $failed += "$($t.ns)/$($t.name)"
    }
}

Write-Host "`n=== 3. Rollout restart -- only the 29 patched utm-services deployments, not the whole namespace (that would also restart untouched infra like service-registry-mysql for nothing) ===" -ForegroundColor Cyan
kubectl -n utm-services rollout restart deployment airspace-data-collector aixm aixm-geozone-adapter alerting-manager altitude-conversion cme-manager cms conflict-detection drone-flight-service dynamic-geozone-atm environment-manager es-migration geoserver-layer-catalog geozone-condition-language keycloak-migration lara-adapter message-logger notam-geozone-adapter operation-plan-report-service operation-plan-service quartz-scheduler ras-manager service-registry smartsis smartsis-event-bridge telemetry-manager user-portal utm-manager utm-geotool
kubectl -n utm-skyzr rollout restart deployment dor-acg dor-be dor-data-importer
kubectl -n utm-core rollout restart deployment geoserver
kubectl -n utm-aviamaps rollout restart deployment flyk

if ($failed.Count -gt 0) {
    Write-Host "`n=== $($failed.Count) patch(es) FAILED, did not restart those: ===" -ForegroundColor Red
    $failed | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }
} else {
    Write-Host "`n=== All 34 patches applied. Rollouts restarting -- this will take a few minutes across 32 pods. ===" -ForegroundColor Green
}
Write-Host "Check back with Claude once rollouts settle to verify traces are landing."
