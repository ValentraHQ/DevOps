# BankID ↔ Keycloak (SKYZR / DOR) — Know-How and Runbook

**Classification:** Internal — DevOps / Systems / Engineering
**Applies to:** `utm-skyzr/dor-keycloak` ("keycloak2"), realm `utm-dor`
**Keycloak in production:** 26.7.3 (`keycloak-lfv-26.7.3:1.0.0`), deployed 2026-09-24
**BankID environment:** test (`appapi2.test.bankid.com`)
**All commands verified against the live cluster:** 2026-09-24

This is a working runbook. Every command below was actually executed; outputs
are quoted as they came back. Where something bit me it is written down as a
trap rather than silently corrected — those are the parts that cost hours.

---

## Table of contents

- [0. Before you start](#0-before-you-start)
- [1. Orientation — which Keycloak, and why it matters](#1-orientation)
- [2. Discovery — finding the integration from scratch](#2-discovery)
- [3. Verifying BankID actually works](#3-verifying-bankid-actually-works)
- [4. Reading the DOR/DMS side](#4-reading-the-dordms-side)
- [5. Building the Keycloak 26.7.3 image](#5-building-the-keycloak-2673-image)
- [6. Deploying](#6-deploying)
- [7. End-to-end test — pilot files a flight plan](#7-end-to-end-test)
- [8. Data flow reference](#8-data-flow-reference)
- [9. Troubleshooting catalogue](#9-troubleshooting-catalogue)
- [10. Environment traps](#10-environment-traps)
- [11. Open items](#11-open-items)
- [12. Artifacts](#12-artifacts)

---

## 0. Before you start

### 0.1 What you need

| Need | Detail |
|---|---|
| kubeconfig | `uas.yaml` (Rancher-issued token, independent of Keycloak — so it keeps working even if Keycloak is down) |
| `kubectl` | any recent version |
| Docker | only for building the image; the Java plugins compile in a container, no local JDK/Maven needed |
| Network | `uas.ops.io` and `harbor.ops.io` to deploy/test; `github.com` + `repo1.maven.org` to build from source |

```bash
export KUBECONFIG=/path/to/uas.yaml
kubectl get nodes
```

> **Trap — Windows / Git-Bash.** `kubectl` is a Windows binary, so `KUBECONFIG`
> must be a *Windows* path even inside Git Bash, and MSYS path translation has
> to be off or arguments like `/opt/keycloak/...` get rewritten to
> `C:/Program Files/...`:
> ```bash
> export KUBECONFIG='C:\Users\you\LFV\uas.yaml'
> export MSYS_NO_PATHCONV=1
> ```
> Symptoms if you forget: `Unable to connect to the server: dial tcp 127.0.0.1:8080`,
> or `the working directory 'C:/Program Files/Git/src' is invalid`.

### 0.2 Shorthand used throughout

```bash
export NS=utm-skyzr
KC=$(kubectl -n $NS get pod -l app=dor-keycloak -o jsonpath='{.items[0].metadata.name}')
DB=$(kubectl -n utm-core get pod -o name | grep core-database | head -1 | cut -d/ -f2)
FLYK=$(kubectl -n utm-aviamaps get pod -l app=flyk -o jsonpath='{.items[0].metadata.name}')
echo "keycloak=$KC  db=$DB  flyk=$FLYK"
```

> `core-database` is recreated from time to time — re-resolve `$DB` rather than
> hard-coding, or you get `Error from server (NotFound): pods "core-database-..." not found`.

Database credentials (two different databases, two different users):

```bash
# Keycloak's own schema: dor_keycloak
KU=$(kubectl  -n $NS get secret dor-keycloak -o jsonpath='{.data.KC_DB_USERNAME}' | base64 -d)
KPW=$(kubectl -n $NS get secret dor-keycloak -o jsonpath='{.data.KC_DB_PASSWORD}' | base64 -d)
# DOR/DMS application schema: dor_be
DU=$(kubectl  -n $NS get secret dor-be -o jsonpath='{.data.DMS_DB_USERNAME}' | base64 -d)
DPW=$(kubectl -n $NS get secret dor-be -o jsonpath='{.data.DMS_DB_PASSWORD}' | base64 -d)
```

---

## 1. Orientation

**There are two Keycloaks. Confusing them wastes a day.**

| | `utm-core/keycloak` | `utm-skyzr/dor-keycloak` |
|---|---|---|
| Nickname | core / SWIM | **keycloak2 / DOR** |
| Version | 23.0.5 | **26.7.3** |
| Public path | `/keycloak/auth` | **`/keycloak2/auth`** |
| Realm | `swim` | **`utm-dor`** |
| Database | `keycloak` | **`dor_keycloak`** |
| Purpose | machine / service identity | **human end-user identity** |
| BankID | ✗ none | ✓ **yes** |
| Trusted by `operation-plan-service` | ✓ | ✗ |

Prove it in one command:

```bash
for d in utm-core/keycloak utm-skyzr/dor-keycloak utm-skyzr/dor-acg-keycloak; do
  ns=${d%%/*}; name=${d##*/}
  p=$(kubectl -n $ns get pod -o name | grep "$name" | head -1 | cut -d/ -f2)
  echo "--- $d ($p)"
  kubectl -n $ns exec $p -- sh -c \
    'ls -1 /opt/keycloak/providers/; cat /opt/keycloak/version.txt' \
    2>/dev/null | sed 's/^/    /'
done
```

Real output:

```
--- utm-core/keycloak (keycloak-6544d7fff4-shzdk)
    README.md
    keycloak-redirect-idp-plugin-1.0.0.jar
    smartsis-listener-1.1.0.jar
    Keycloak - Version 23.0.5
--- utm-skyzr/dor-keycloak (dor-keycloak-8465fbdc8-wnrl5)
    apple-social-identity-provider-1.0.2.jar
    bankid4keycloak-26.0.0-kc26.7.3.jar
    basic-storage-provider-1.0.jar
    README.md
    sweden-connect-provider-0.5.0.jar
    Keycloak - Version 26.7.3
--- utm-skyzr/dor-acg-keycloak (dor-acg-keycloak-775b864898-588nh)
    README.md
    apple-social-identity-provider-1.0.2.jar
    basic-storage-provider-1.0.jar
    datanor-keycloak-extensions-v17.14032025.jar
    Keycloak - Version 17.0.1
```

`utm-core/keycloak` has **no BankID provider at all**.

> Use `cat /opt/keycloak/version.txt`, **not** `kc.sh --version | head -1`.
> On 26.7.3 the first line of `kc.sh` output is
> `Appending additional Java properties to JAVA_OPTS`, so `head -1` prints the
> banner instead of the version. Also note `README.md` in `providers/` ships
> with the base image and is not a provider.

> **About the older document.** *BankID-Keycloak_Integration v1* (2026-09-22)
> describes a **local Docker PoC** — KC 23.0.5, `start-dev`, H2 in memory,
> `master` realm, `localhost:8080`. That PoC was real and worked, but it is not
> this deployment. Anyone following it against the cluster is on the wrong
> server.

---

## 2. Discovery

How to re-derive the whole picture with no prior knowledge.

### 2.1 Which deployments are identity-related

```bash
kubectl get deploy -A -o json | python -c "
import sys,json
for i in json.load(sys.stdin)['items']:
    n=i['metadata']['name']; ns=i['metadata']['namespace']
    if any(k in n.lower() for k in ('keycloak','auth','idp','bankid')):
        c=i['spec']['template']['spec']['containers'][0]
        print('  %-14s %-22s ready=%s/%s'%(ns,n,i['status'].get('readyReplicas',0),i['spec'].get('replicas')))
        print('       %s'%c['image'])
"
```

### 2.2 Keycloak's configuration

Config and secrets arrive via `envFrom`, so the pod spec's `env` list looks
empty. Read the ConfigMap instead:

```bash
kubectl -n $NS get cm dor-keycloak -o json | python -c "
import sys,json
for k,v in sorted(json.load(sys.stdin)['data'].items()): print('  %-36s %s'%(k,str(v)[:90]))
"
kubectl -n $NS get secret dor-keycloak -o json | python -c "
import sys,json; print('  keys: '+', '.join(sorted(json.load(sys.stdin)['data'])))
"
```

Values that matter:

```
  KC_DB                                mariadb
  KC_DB_URL_DATABASE                   dor_keycloak
  KC_DB_URL_HOST                       core-database.utm-core.svc.cluster.local
  KC_HTTP_RELATIVE_PATH                /keycloak2/auth
  KC_PROXY                             edge      <-- Hostname v1, see 6.3
  KC_HOSTNAME_STRICT_HTTPS             false     <-- Hostname v1, see 6.3
  KEYCLOAK_SKYZR_WEBHOOK_REGISTER_URL  https://uas.ops.io/svc-pm/api/users
```

### 2.3 Certificates and mounts

```bash
kubectl -n $NS get deploy dor-keycloak -o json | python -c "
import sys,json
t=json.load(sys.stdin)['spec']['template']['spec']; c=t['containers'][0]
print(' MOUNTS:')
for m in c.get('volumeMounts',[]): print('  %-22s -> %-40s ro=%s'%(m['name'],m['mountPath'],m.get('readOnly')))
print(' VOLUMES:')
for v in t.get('volumes',[]):
    k=[x for x in v if x!='name'][0]
    print('  %-22s %-14s %s'%(v['name'],k,(v[k].get('secretName') or v[k].get('name') or '')))
"
```

```
 MOUNTS:
  truststore             -> /opt/keycloak/conf/truststore
  wildcard-tls           -> /opt/keycloak/conf/tls              ro=True
  bankid-tls             -> /opt/keycloak/conf/tls-bankid       ro=True
 VOLUMES:
  bankid-tls             secret         bankid-tls
```

What the BankID secret holds — names and sizes only, never key material:

```bash
kubectl -n $NS get secret bankid-tls -o json | python -c "
import sys,json,base64
for k,v in sorted(json.load(sys.stdin)['data'].items()):
    print('  %-38s %6d bytes'%(k,len(base64.b64decode(v))))
"
```

```
  Test_BankID_SSL_Root_CA_v1_Test.pem      2078 bytes
  keystore.p12                             2979 bytes
  truststore.p12                           1878 bytes
```

### 2.4 Which identity providers are configured

Use the Admin API — it reflects the running server, not raw tables.

```bash
BASE=https://uas.ops.io/keycloak2/auth
U=$(kubectl -n $NS get secret dor-keycloak -o jsonpath='{.data.KEYCLOAK_ADMIN}' | base64 -d)
P=$(kubectl -n $NS get secret dor-keycloak -o jsonpath='{.data.KEYCLOAK_ADMIN_PASSWORD}' | base64 -d)
TOKEN=$(curl -s -d client_id=admin-cli -d username="$U" -d "password=$P" -d grant_type=password \
        "$BASE/realms/master/protocol/openid-connect/token" \
        | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

curl -s -H "Authorization: Bearer $TOKEN" "$BASE/admin/realms/utm-dor/identity-provider/instances" \
| python -c "
import sys,json
for i in json.load(sys.stdin):
    print('  %-24s providerId=%-8s enabled=%s'%(i['alias'],i['providerId'],i['enabled']))
"
```

```
  bankid                   providerId=bankid   enabled=True
  sweden-connect-sandbox   providerId=saml     enabled=False
```

> Admin tokens live ~60 s. Fetch and use inside one script; caching across steps
> gives `401 Unauthorized` on the second call.

> `providerId=saml` is **not** stock Keycloak SAML — `sweden-connect-provider`
> overrides the built-in `saml` factory
> (`se.swedenconnect.keycloak.saml.SwedenConnectSAMLIdentityProviderFactory`).
> Removing that jar silently changes this IdP's behaviour.

### 2.5 BankID provider configuration (secrets masked)

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
     "$BASE/admin/realms/utm-dor/identity-provider/instances/bankid" \
| python -c "
import sys,json
for k,v in sorted(json.load(sys.stdin)['config'].items()):
    if any(s in k.lower() for s in ('password','secret')): v='<set, %d chars>'%len(v)
    print('  %-30s %s'%(k,v))
"
```

```
  bankid_apiurl                  https://appapi2.test.bankid.com
  bankid_keystore_file           /opt/keycloak/conf/tls-bankid/keystore.p12
  bankid_keystore_password       <set, 9 chars>
  bankid_truststore_file         /opt/keycloak/conf/tls-bankid/truststore.p12
  bankid_require_nin             false
  bankid_save_nin_hash           false
  bankid_show_qr_code            true
  clientId                       lfv
  clientSecret                   <set, 3 chars>
```

`clientId` / `clientSecret` are demanded by Keycloak's generic IdP form and are
**unused by the BankID protocol**. Any placeholder works.

### 2.6 Creating or updating the BankID IdP

```bash
curl -s -X POST "$BASE/admin/realms/utm-dor/identity-provider/instances" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{
  "alias":"bankid","providerId":"bankid","enabled":true,
  "config":{
    "clientId":"lfv","clientSecret":"lfv",
    "bankid_apiurl":"https://appapi2.test.bankid.com",
    "bankid_keystore_file":"/opt/keycloak/conf/tls-bankid/keystore.p12",
    "bankid_keystore_password":"qwerty123",
    "bankid_privatekey_password":"qwerty123",
    "bankid_truststore_file":"/opt/keycloak/conf/tls-bankid/truststore.p12",
    "bankid_truststore_password":"qwerty123",
    "bankid_show_qr_code":"true",
    "bankid_require_nin":"false",
    "bankid_save_nin_hash":"false"
  }}'
```

Expect **201**. If the alias exists you get 409 — use `PUT .../instances/bankid`
to update in place instead.

---

## 3. Verifying BankID actually works

Three independent checks, cheapest first.

### 3.1 Do the certificates load, and when do they expire?

The pod has `keytool`. The BankID **test** keystore password is public
(`qwerty123`, published by BankID with the test certificates), so this is safe
as written. A production certificate's password must come from the IdP config
or a secret.

```bash
kubectl -n $NS exec $KC -c dor-keycloak -- \
  keytool -list -v -keystore /opt/keycloak/conf/tls-bankid/keystore.p12 \
          -storetype PKCS12 -storepass qwerty123 2>&1 \
| grep -iE 'alias name|entry type|owner|issuer|valid from'
```

```
Alias name: 1
Entry type: PrivateKeyEntry
Owner:  CN=FP Testcert 5, OID.2.5.4.41=Test av BankID, SERIALNUMBER=5566304928,
        O=Testbank A AB (publ), C=SE
Issuer: CN=Testbank A RP CA v1 for BankID Test, ...
Valid from: Tue Jul 02 22:00:00 GMT 2024 until: Mon May 28 21:59:59 GMT 2029
```

```bash
kubectl -n $NS exec $KC -c dor-keycloak -- \
  keytool -list -v -keystore /opt/keycloak/conf/tls-bankid/truststore.p12 \
          -storetype PKCS12 -storepass qwerty123 2>&1 | grep -iE 'alias|owner|valid from'
```

```
Alias name: bankid test ca
Owner: CN=Test BankID SSL Root CA v1 Test, OU=Infrastructure CA,
       O=Finansiell ID-Teknik BID AB
Valid from: Fri Nov 21 12:39:31 GMT 2014 until: Sun Dec 31 12:39:31 GMT 2034
```

### 3.2 Does mutual TLS to BankID succeed? — the decisive test

Proves egress, DNS, the server certificate chain, **and that BankID accepts our
relying-party certificate**.

> **Trap.** The Keycloak pod has **no `curl`, `wget`, `openssl`, `python` or
> `tar`** — only `java`, `keytool` and `bash`. Check:
> ```bash
> kubectl -n $NS exec $KC -c dor-keycloak -- sh -c \
>   'for t in curl wget openssl java keytool python3 tar; do printf "%-9s " $t; command -v $t || echo "-"; done'
> ```
> So **`kubectl cp` does not work** — it needs `tar` in the container:
> ```
> OCI runtime exec failed: exec: "tar": executable file not found in $PATH
> ```
> Stream the file in instead.

`e2e/BankidProbe.java` does what the provider's `SimpleBankidClient` does:

```bash
kubectl -n $NS exec -i $KC -c dor-keycloak -- sh -c 'cat > /tmp/BankidProbe.java' < e2e/BankidProbe.java
kubectl -n $NS exec    $KC -c dor-keycloak -- java /tmp/BankidProbe.java
kubectl -n $NS exec    $KC -c dor-keycloak -- rm -f /tmp/BankidProbe.java
```

Expected:

```
java    : 21.0.12.1
target  : https://appapi2.test.bankid.com
keystore: loaded, aliases=[1]
truststore: loaded, aliases=[bankid test ca]

--- TLS ---
cipher  : TLS_AES_256_GCM_SHA384
server  : CN=appapi2.test.bankid.com, SERIALNUMBER=5566304928,
          OU=BankID Test, O=Finansiell ID-Teknik BID AB, C=SE

--- HTTP ---
status  : 200  (1316 ms)
body    : {"orderRef":"01a0d29d-...","autoStartToken":"d534757e-...",
           "qrStartToken":"de366bbb-...","qrStartSecret":"dc890c37-..."}

--- VERDICT ---
mTLS OK and BankID accepted the relying-party certificate.
```

`HTTP 200` with a live `orderRef` is conclusive. The probe sends no personal
number and completes no authentication for a real person; the order it opens
expires unused in ~30 s.

Reading the result:

| Result | Meaning |
|---|---|
| 200 + `orderRef` | everything works |
| connect timeout / `UnknownHostException` | cluster egress or DNS blocked |
| `SSLHandshakeException` … `unable to find valid certification path` | wrong truststore |
| 401 / 403 | reachable, but BankID **rejected our RP certificate** (expired or wrong cert) |

### 3.3 Does BankID appear on the login page?

```bash
curl -s "https://uas.ops.io/keycloak2/auth/realms/utm-dor/protocol/openid-connect/auth\
?client_id=frontend&redirect_uri=https%3A%2F%2Fuas.ops.io%2Favm%2F&response_type=code&scope=openid&state=probe" \
| grep -oE 'broker/[a-zA-Z0-9_-]+/login' | sort -u
```

```
broker/bankid/login
```

### 3.4 Has anyone ever actually logged in with BankID?

```bash
kubectl -n utm-core exec $DB -- sh -c "mysql -u'$KU' -p'$KPW' -t -e \"
SELECT fi.IDENTITY_PROVIDER, COUNT(*) AS links
  FROM dor_keycloak.FEDERATED_IDENTITY fi
  JOIN dor_keycloak.USER_ENTITY u ON u.ID=fi.USER_ID
  JOIN dor_keycloak.REALM r       ON r.ID=u.REALM_ID
 WHERE r.NAME='utm-dor' GROUP BY fi.IDENTITY_PROVIDER;\""
```

An empty result means **no BankID browser ceremony has ever completed here** —
which is the current state. The API leg is proven; the interactive leg is not.
Closing it needs a human with a BankID test identity from
<https://developers.bankid.com/test-portal/test-information>.

---

## 4. Reading the DOR/DMS side

### 4.1 Schema trap — column names move between Keycloak versions

```bash
kubectl -n utm-core exec $DB -- sh -c "mysql -u'$KU' -p'$KPW' -N -B -e \"
SELECT GROUP_CONCAT(COLUMN_NAME SEPARATOR ' ') FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA='dor_keycloak' AND TABLE_NAME='IDENTITY_PROVIDER';\""
```

On 25/26 this returns `PROVIDER_ALIAS`, **not** `ALIAS`. Using `ALIAS` gives:

```
ERROR 1054 (42S22): Unknown column 'i.ALIAS' in 'field list'
```

Similarly `REALM` has `REG_EMAIL_AS_USERNAME`, not
`REGISTRATION_EMAIL_AS_USERNAME`. **Check `information_schema.COLUMNS` first,
every time.**

> `bit(1)` columns render blank under `mysql -t`. Cast them:
> `CAST(ENABLED AS UNSIGNED)`.

### 4.2 Realm registration settings

```bash
kubectl -n utm-core exec $DB -- sh -c "mysql -u'$KU' -p'$KPW' -t -e \"
SELECT NAME, LOGIN_THEME,
       CAST(REGISTRATION_ALLOWED AS UNSIGNED)   reg,
       CAST(VERIFY_EMAIL AS UNSIGNED)           verify_email,
       CAST(LOGIN_WITH_EMAIL_ALLOWED AS UNSIGNED) login_email
  FROM dor_keycloak.REALM WHERE NAME='utm-dor'\G\""
```

### 4.3 The DMS user model (`dor_be`)

```bash
kubectl -n utm-core exec $DB -- sh -c "mysql -u'$DU' -p'$DPW' -t -e \"
SELECT id,email,active,allow_login,verified,verified_by,last_logged_in_to
  FROM dor_be.users ORDER BY id;
SELECT ur.user_id,u.email,ur.role_id,r.role_name,ur.org_id
  FROM dor_be.user_roles ur
  JOIN dor_be.users u ON u.id=ur.user_id
  JOIN dor_be.roles r ON r.role_id=ur.role_id ORDER BY ur.user_id;
SELECT org_id,operator_id,name,active,verified FROM dor_be.organisations;\""
```

Roles: 1 `default_user`, 2 `operator_member`, 3 `operator_administrator`,
4 `pilot`, 5 `administrator`\*, 6 `customer_support`\*, 7 `interim_user`,
8 `interim_org_user`, 9 `api_client_app`\*  (\* internal).

`users.last_logged_in_to` holds the **currently selected organisation**.
`verified_by = 'MANUAL'` on the working accounts — approval is a human step today.

### 4.4 dor-be's API contract

dor-be publishes OpenAPI; it is the fastest way to answer "which endpoint does X".

```bash
TOK=$(curl -s -d client_id=frontend -d username='<user>' -d password='<pw>' -d grant_type=password \
      "$BASE/realms/utm-dor/protocol/openid-connect/token" \
      | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

curl -s -H "Authorization: Bearer $TOK" https://uas.ops.io/svc-pm/v3/api-docs > dorbe-api.json
python -c "
import json,io
d=json.load(io.open('dorbe-api.json',encoding='utf-8-sig'))
for path in sorted(d['paths']):
    for m,op in sorted(d['paths'][path].items()):
        if m in ('get','post','put','patch','delete'):
            print('  %-7s %-52s %s'%(m.upper(),path,(op.get('summary') or '')[:50]))
"
```

> Note the `utf-8-sig` — the response carries a BOM and plain `utf-8` fails.

Endpoints worth knowing:

| Endpoint | Note |
|---|---|
| `POST /api/users/create` | *"used by the keycloak webhook"* — the DMS bridge |
| `POST /api/import/registration/pin` | import a national operator registration |
| `POST /api/organisations/createeu` | operator registered in another EU country |
| `POST /api/invites/create` | `{email, language}` — invite into the current operator |
| `POST /api/invites/accept/{id}` | accept an invite |
| `POST /api/operators/{id}/approve` | *"Set operator as verified"* |
| `GET  /api/users/{email}/permissions` | effective roles + permissions |
| `POST /api/organisations/switch/{id}` | set the current operator |

> **There is no `POST /api/organisations`.** That path answers
> `Allow: GET,HEAD,OPTIONS`. Operator identity is anchored to a national
> registration — see §8.3.

### 4.5 Who changed what, and when

Keycloak records admin actions. This is how the disabled `sweden-connect-sandbox`
was attributed to a human rather than the upgrade:

```bash
kubectl -n utm-core exec $DB -- sh -c "mysql -u'$KU' -p'$KPW' -t -e \"
SELECT FROM_UNIXTIME(ADMIN_EVENT_TIME/1000) ts, OPERATION_TYPE op,
       LEFT(RESOURCE_PATH,50) path, AUTH_USER_ID
  FROM dor_keycloak.ADMIN_EVENT_ENTITY
 WHERE RESOURCE_PATH LIKE '%identity-provider%'
 ORDER BY ADMIN_EVENT_TIME DESC LIMIT 8;\""
```

Resolve `AUTH_USER_ID` to a username:

```bash
kubectl -n utm-core exec $DB -- sh -c "mysql -u'$KU' -p'$KPW' -t -e \"
SELECT u.USERNAME, r.NAME realm FROM dor_keycloak.USER_ENTITY u
  JOIN dor_keycloak.REALM r ON r.ID=u.REALM_ID WHERE u.ID='<auth-user-id>';\""
```

---

## 5. Building the Keycloak 26.7.3 image

Everything lives in `keycloak-skyzr-26/`.

### 5.1 Quick path

```bash
cd keycloak-skyzr-26
./build.sh                 # build + sanity gate
./build.sh --push          # and push to Harbor
./verify.sh harbor.ops.io/utm-devops/keycloak-lfv-26.7.3:1.0.0
```

### 5.2 Building where Maven Central is unreachable

On the Frequentis build hosts only internal names resolve:

```
Could not transfer artifact org.apache.maven.plugins:maven-clean-plugin:pom:3.2.0
  from/to central: repo.maven.apache.org: Temporary failure in name resolution
```

`build.sh` probes for this and falls back to the prebuilt jars. To be explicit:

```bash
PLUGINS=skip ./build.sh --push                              # use providers/ as-is
MAVEN_MIRROR_URL=https://nexus/repository/maven-public/ ./build.sh
https_proxy=http://proxy:3128 ./build.sh
```

`PLUGINS=skip` is safe: the sanity gate still checks the full provider set,
rejects stale duplicate versions, and re-checks that the sweden-connect jar
carries no bundled JAXB.

Expected jars:

```
d85b073b4d6255dc...  apple-social-identity-provider-1.0.2.jar
531cdb742891439b...  bankid4keycloak-26.0.0-kc26.7.3.jar
091360cd94abdb63...  basic-storage-provider-1.0.jar
840c7c1588a99d61...  sweden-connect-provider-0.5.0.jar
```

### 5.3 Reconstructing the build context from a running image

If `providers/` and `themes/` are lost, extract them from the deployed image
rather than hunting for sources:

```bash
IMG=harbor.ops.io/utm-devops/keycloak-lfv-25.0.6:1.0.1
docker pull $IMG
CID=$(docker create $IMG)
docker cp $CID:/opt/keycloak/providers/. ./providers/
docker cp $CID:/opt/keycloak/themes/.    ./themes/
docker rm -f $CID
```

Recover the recipe of any image:

```bash
docker history --no-trunc --format '{{.CreatedBy}}' $IMG | head -25
```

Themes include **`lfv`**, the realm's configured login theme — required, not
cosmetic.

### 5.4 The three compatibility fixes

None optional; each broke the build or the server.

#### (a) sweden-connect 0.4.10 → 0.5.0 — JAXB collision

`kc.sh build` failed:

```
ERROR: Unable to build configuration.xml JAXBContext
ERROR: jakarta.xml.bind.JAXBContextFactory:
       org.glassfish.jaxb.runtime.v2.JAXBContextFactory not a subtype
```

Diagnose by looking inside the jars:

```bash
for j in providers/*.jar; do
  n=$(unzip -l "$j" | grep -icE 'jakarta/xml/bind|org/glassfish/jaxb')
  s=$(unzip -l "$j" | grep -cE 'META-INF/services/jakarta.xml.bind')
  printf "  %-44s jaxb-classes=%-5s jaxb-services=%s\n" "$(basename $j)" "$n" "$s"
done
```

```
  sweden-connect-provider-0.4.10.jar    jaxb-classes=950   jaxb-services=1
```

0.4.10 is a 12.9 MB shaded jar bundling its own JAXB implementation, overriding
Keycloak's. **v0.5.0 is the first release targeting `keycloak.version 26.7.3`,
and it stopped shading** — 81 KB, zero JAXB classes.

```bash
git clone https://github.com/swedenconnect/keycloak-plugins
cd keycloak-plugins && git archive v0.5.0 | tar -x -C ../swedenconnect-src
docker run --rm -v "$PWD/../swedenconnect-src:/src" -w /src maven:3.9-eclipse-temurin-21 \
  mvn -B clean package -DskipTests -pl sweden-connect-provider -am
```

#### (b) bankid4keycloak — `retrieveToken` signature

Keycloak 26.7 widened `UserAuthenticationIdentityProvider#retrieveToken`:

```
BankidIdentityProvider is not abstract and does not override abstract method
retrieveToken(KeycloakSession,FederatedIdentityModel,UserSessionModel,UserModel)
```

Add the four-argument overload in
`bankid-src/src/main/java/org/keycloak/broker/bankid/BankidIdentityProvider.java`,
delegating to the existing two-argument one:

```java
@Override
public Response retrieveToken(KeycloakSession session, FederatedIdentityModel identity,
        UserSessionModel userSession, UserModel user) {
    return retrieveToken(session, identity);
}
```

BankID issues no OAuth token of its own, so neither extra argument changes the
result. Remember `import org.keycloak.models.UserSessionModel;`.

> Upstream master targets 26.5.6. The working copy at `../bankid4keycloak`
> carries the *opposite* patch — downgrading the import to
> `IdentityProvider.AuthenticationCallback` for a pinned 26.4.6. **Do not apply
> that here**; 26.7.3 wants the newer API.

#### (c) bankid4keycloak — protostream version (the dangerous one)

This **passed `kc.sh build`** and then killed the server at startup:

```
ISPN000659: Component SerializationContextRegistry failed to start
org/infinispan/protostream/annotations/impl/GeneratedMarshallerBase
```

bankid4keycloak generates Infinispan marshallers at compile time. Upstream pins
protostream **15.0.13.Final**; Keycloak 26.7.3 ships **6.0.7**. Find what the
base carries, then match it:

```bash
docker run --rm --entrypoint sh quay.io/keycloak/keycloak:26.7.3 -c \
  'ls /opt/keycloak/lib/lib/main/ | grep -i protostream'
# org.infinispan.protostream.protostream-6.0.7.jar
```

In `bankid-src/pom.xml`:

```xml
<keycloak.version>26.7.3</keycloak.version>
<protostream.version>6.0.7</protostream.version>
```

Rebuild:

```bash
docker run --rm -v "$PWD/bankid-src:/src" -w /src maven:3.9-eclipse-temurin-21 \
  mvn -B clean package -DskipTests
cp bankid-src/target/bankid4keycloak-26.0.0-SNAPSHOT.jar \
   providers/bankid4keycloak-26.0.0-kc26.7.3.jar
```

> **On every future Keycloak bump, re-check this pin.** A mismatch produces an
> image that builds cleanly and a server that will not start.

### 5.5 Base image

`build.sh` defaults to `quayio.dev.dkr.frequentis.frq/keycloak/keycloak:26.7.3`,
which resolves only on the Frequentis network. It is byte-identical to the
public image — verified:

```bash
docker pull quay.io/keycloak/keycloak:26.7.3
a=$(docker image inspect quayio.dev.dkr.frequentis.frq/keycloak/keycloak:26.7.3 --format '{{json .RootFS.Layers}}')
b=$(docker image inspect quay.io/keycloak/keycloak:26.7.3 --format '{{json .RootFS.Layers}}')
[ "$a" = "$b" ] && echo "IDENTICAL layers" || echo "DIFFERENT"
```

So develop against `quay.io`, build for real against the mirror:

```bash
docker build --build-arg KEYCLOAK_BASE=quay.io/keycloak/keycloak:26.7.3 -t kc-local .
```

### 5.6 OpenShift compatibility

`restricted-v2` runs containers with a **random uid** but always gid 0:

```bash
docker run --rm --user 1000670000:0 --entrypoint sh <image> -c '
  id
  for d in /opt/keycloak/data /opt/keycloak/data/tmp /opt/keycloak/conf \
           /opt/keycloak/conf/tls /opt/keycloak/conf/tls-bankid \
           /opt/keycloak/conf/truststore /opt/keycloak/saml-keys /opt/keycloak/lib/quarkus; do
    touch $d/.w 2>/dev/null && { echo "OK   $d"; rm -f $d/.w; } || echo "FAIL $d"
  done'
```

The Dockerfile achieves this with
`chgrp -R 0 /opt/keycloak && chmod -R g=u /opt/keycloak` — `g=u` copies each
file's user bits to its group bits, Red Hat's documented pattern and safer than
a blanket `777`.

> `readOnlyRootFilesystem` is **not** achievable: Keycloak writes
> `/opt/keycloak/data/tmp` (theme gzip cache) and `lib/quarkus`. Mount those as
> volumes if your SCC demands it.

### 5.7 Verify before deploying

```bash
./verify.sh harbor.ops.io/utm-devops/keycloak-lfv-26.7.3:1.0.0
VERBOSE=1 ./verify.sh <image>       # show readiness probes
```

15 checks, all must pass:

```
  PASS  runs as uid 1000 (not root)
  PASS  arbitrary uid (1000670000:0) can write all runtime dirs
  PASS  server reaches health/ready=200 under arbitrary uid
  PASS  no classloader or Infinispan errors
  PASS  /health/ready -> 200     PASS  /health/live -> 200     PASS  /metrics -> 200
  PASS  provider registered: "bankid"
  PASS  provider registered: "saml"
  PASS  provider registered: "apple"
  PASS  provider registered: "skyzr-dms-webhook-listener"
  PASS  provider registered: "skyzr-dms-storage-interceptor"
  PASS  reports Keycloak 26.7.3
  PASS  BankID IdP instance created (HTTP 201)
```

`verify.sh` boots the server and drives the Admin API deliberately — **`kc.sh build`
succeeding does not mean the providers work**, as fix (c) proved.

---

## 6. Deploying

### 6.1 Roll out

```bash
kubectl -n $NS set image deploy/dor-keycloak \
  dor-keycloak=harbor.ops.io/utm-devops/keycloak-lfv-26.7.3:1.0.0
kubectl -n $NS rollout status deploy/dor-keycloak --timeout=300s
```

### 6.2 Confirm the upgrade landed

```bash
KC=$(kubectl -n $NS get pod -l app=dor-keycloak -o jsonpath='{.items[0].metadata.name}')
kubectl -n $NS logs $KC -c dor-keycloak | grep -iE 'Keycloak .* on JVM|Profile prod'
kubectl -n $NS logs $KC -c dor-keycloak | grep -iE '\bERROR\b|Exception|ISPN000659|not a subtype'

kubectl -n utm-core exec $DB -- sh -c "mysql -u'$KU' -p'$KPW' -t -e \"
SELECT ID,DATEEXECUTED FROM dor_keycloak.DATABASECHANGELOG
 ORDER BY DATEEXECUTED DESC LIMIT 6;\""
```

```
Keycloak 26.7.3 on JVM (powered by Quarkus 3.33.3.1) started in 7.555s.
Listening on: http://0.0.0.0:8080 and https://0.0.0.0:8443.
Management interface listening on https://0.0.0.0:9000.

| 26.7.0-cluster-event          | 2026-09-24 05:28:14 |
| 26.7.0-add-timestamps-client  | 2026-09-24 05:28:12 |
```

Identify exactly which image is running — tags lie, digests do not:

```bash
kubectl -n $NS get pod -l app=dor-keycloak \
  -o jsonpath='{.items[0].status.containerStatuses[0].imageID}{"\n"}'
```

List what Harbor holds, with push times (internal CA ⇒ `-k`):

```bash
curl -sk "https://harbor.ops.io/api/v2.0/projects/utm-devops/repositories/keycloak-lfv-26.7.3/artifacts?with_tag=true" \
| python -c "
import sys,json
for a in json.load(sys.stdin):
    print('  %-8s push=%s size=%sMB digest=%s'%(
      ','.join(t['name'] for t in a.get('tags') or []), a['push_time'][:19],
      round(a['size']/1048576), a['digest'][7:19]))
"
```

> **Naming trap.** Two repositories differ only by `-` vs `.`:
> `keycloak-lfv-26-7-3` and `keycloak-lfv-26.7.3`. The **dotted** one is what
> `build.sh` targets and what is deployed. The hyphenated repo holds earlier
> builds (1.0.0 / 1.0.1 / 1.0.3) whose higher tag numbers make them look newer.
> **Compare `push_time`, not tag numbers.**

### 6.3 Configuration changes that must accompany the image

Keycloak 26 deprecated the Hostname v1 options still in `cm/dor-keycloak`:

| Key | Action | Why |
|---|---|---|
| `KC_PROXY: edge` | **remove** | superseded by `KC_PROXY_HEADERS=xforwarded`, baked into the image — and the ConfigMap value **overrides** it |
| `KC_HOSTNAME_STRICT_HTTPS` | **remove** | Hostname v1 |
| `KC_HOSTNAME` | **add** `https://uas.ops.io/keycloak2/auth` | Hostname v2 wants the full public URL |

Reproduce the warning without touching the cluster:

```bash
docker run --rm -e KC_DB=dev-mem -e KC_HTTP_ENABLED=true -e KC_HOSTNAME_STRICT=false \
  -e KC_HOSTNAME_STRICT_HTTPS=false -e KC_PROXY=edge \
  -e KC_BOOTSTRAP_ADMIN_USERNAME=a -e KC_BOOTSTRAP_ADMIN_PASSWORD=a \
  quay.io/keycloak/keycloak:26.7.3 start 2>&1 | grep -i 'WARNING'
```

```
WARNING: Hostname v1 options [proxy, hostname-strict-https] are still in use
WARNING: With HTTPS not enabled, `proxy-headers` unset ... requests from the
proxy to the server will fail CORS checks with 403s because the wrong origin
will be determined.
```

dor-keycloak sits behind nginx ingress, so this is the highest-risk item.

Apply:

```bash
kubectl -n $NS patch cm dor-keycloak --type=json -p '[
  {"op":"remove","path":"/data/KC_PROXY"},
  {"op":"remove","path":"/data/KC_HOSTNAME_STRICT_HTTPS"},
  {"op":"add","path":"/data/KC_HOSTNAME","value":"https://uas.ops.io/keycloak2/auth"}
]'
kubectl -n $NS rollout restart deploy/dor-keycloak
```

### 6.4 Probes

The deployment has **no readinessProbe**. In 26.x health is on the **management
port 9000 over HTTPS** (because `KC_HTTPS_CERTIFICATE_FILE` is set), so
`scheme: HTTPS` is mandatory:

```yaml
ports:
  - { name: http,       containerPort: 8080 }
  - { name: management, containerPort: 9000 }
readinessProbe:
  httpGet: { path: /health/ready, port: management, scheme: HTTPS }
  initialDelaySeconds: 30
  periodSeconds: 10
  failureThreshold: 30        # first boot is 60-105 s
livenessProbe:
  httpGet: { path: /health/live, port: management, scheme: HTTPS }
  initialDelaySeconds: 120
  periodSeconds: 30
```

### 6.5 Rollback

```bash
kubectl -n $NS rollout undo deploy/dor-keycloak
```

> The 25 → 26 **schema migration is not reversible**. Rolling the image back
> onto a migrated database is unsupported. Back up `dor_keycloak` before
> upgrading and rehearse on a copy.

---

## 7. End-to-end test

**Goal:** a pilot logs in and files a flight plan in FLYK.

### 7.1 One command

```powershell
.\e2e\flyk-file-flight-plan.ps1 `
    -Email    e2e-dronetest@ops.io `
    -Password '<password>' `
    -Operator PH-DRN-REG-AX1B2 `
    -Withdraw
```

Needs only network access to `https://uas.ops.io` — no cluster access, no
browser. `-Withdraw` retires the plan afterwards.

### 7.2 Preconditions

| Requirement | Check |
|---|---|
| Keycloak user in realm `utm-dor` | password-grant token succeeds |
| DMS user row | `GET /svc-pm/api/users/{email}/permissions` → 200 |
| Member of an operator | `GET /svc-pm/api/organisations` → a row, not `[]` |
| `allowOperationPlans = true` | same call |
| `verified = 1` | **not required** — see §8.4 |

Account used: `e2e-dronetest@ops.io`, org 1 (`Surely Win Dilag`,
`PH-DRN-REG-AX1B2`), roles `pilot` + `operator_member`, `verified = 0`.

Creating such an account from scratch:

```bash
# 1. register through the real form (only a genuine REGISTER event fires the webhook)
#    -> browser, or scrape the form at /keycloak2/auth/realms/utm-dor/login-actions/registration
# 2. confirm the DMS row appeared (~10 s later)
kubectl -n utm-core exec $DB -- sh -c "mysql -u'$DU' -p'$DPW' -t -e \"
SELECT id,email,active,allow_login,verified FROM dor_be.users ORDER BY id DESC LIMIT 3;\""
# 3. an operator_administrator invites them, from https://uas.ops.io/pm/ or:
curl -s -X POST https://uas.ops.io/svc-pm/api/invites/create \
  -H "Authorization: Bearer <admin's utm-dor token>" -H 'Content-Type: application/json' \
  -d '{"email":"e2e-dronetest@ops.io","language":"en"}'
```

> Creating the user via the **Admin API will not work** for this purpose — it
> fires an admin event, not a `REGISTER` event, so `skyzr-dms-webhook-listener`
> never runs and no DMS row is created.

### 7.3 What the script does, step by step

These are the same calls FLYK's browser client makes.

**Step 1 — start the OIDC login.** `GET /avm/auth/skyzr/login` redirects to
Keycloak with a generated `state`.

**Step 2 — post the Keycloak login form.** Scrape the form action, POST
`username` / `password` / `credentialId`. Ends at
`/avm/?code=<authorization code>`.

**Step 3 — bind the FLYK session.**

```
POST /avm/auth/skyzr/hello
x-id: <uuid you invent>
Content-Type: application/json

{"authorizationCode":"<code>"}
```

FLYK exchanges the code, calls dor-be for the profile and `/api/organisations`,
and fills `session.email` + `db.tokens.data[email].user`. Response:

```json
{"user":{"email":"e2e-dronetest@ops.io","orgId":1,"active":true,
 "registrationNumbers":["PH-DRN-REG-AX1B2"],
 "company":{"name":"Surely Win Dilag","allowOperationPlans":true}}}
```

> **The single biggest gotcha: FLYK sessions are neither cookies nor bearer
> tokens.** `server/db/sessions.js` keys on an **`x-id` header** (or `?id=`),
> which the browser keeps in `localStorage['CapacitorStorage.id']`:
> ```js
> const id = req.get("x-id") || req.query.id;
> if (!id) { ... return next(); }   // req.session stays undefined
> ```
> Send a Keycloak bearer token instead and you get a bare **HTTP 500**, whose
> real cause appears only in the pod log:
> ```
> TypeError: Cannot destructure property 'email' of 'req.session' as it is undefined
>     at /app/server/utm/index.js:1278
> ```

**Step 4 — borrow geometry.** Pull an existing plan from
`GET /avm/utm/operationplans.json` and reuse its polygon, so the test exercises
the flow rather than airspace validity.

> `operationplans.json` returns `[]` until the session exists — it filters by
> the session's operator. An empty list is a *session* symptom, not "no plans".

**Step 5 — file the plan.**

```
POST /avm/utm/operationplan/notify
x-id: <same uuid>
Content-Type: application/json
```

Despite the name, this is the create call. FLYK checks
`user.registrationNumbers` contains `body.operator` (else **401**), stamps
`submitTime`/`updateTime`, forces `altitudeType`, strips `suppress`, mints a
**swim `client_credentials`** token, and calls
`operation-plans/propose-and-authorize`.

**Step 6 — validate.** Re-read `operationplans.json` and
`GET /avm/utm/authorization/{id}`.

### 7.4 Payload shape

Derived from the service's own `MODEL_VALIDATION_ERROR` — which helpfully lists
all 28 accepted properties — and from a live plan. Not from documentation.

```
operationPlan
  operationPlanId   uuid          REQUIRED
  version           uuid          REQUIRED        <-- easy to miss
  operator          string        REQUIRED, must match a registrationNumber
  state             PROPOSED
  modeOfOperation   REMOTELY_PILOTED_VLOS | REMOTELY_PILOTED_BVLOS
  swarmSize, minContOpTime, formationId, formationOpIds
  publicInfo        { title, description }
  priority          { priorityText, priorityLevelSimple }
  takeoffLocation / landingLocation / gcsLocation / controllerLocation
                    GeoJSON Point
  contactDetails    { firstName, lastName, emails[], phones[], comments[], fax }
  flightDetails     { flightNumber, flightType, flightComment }
  aircraftInfos     { maxTakeoffMassGrams, identificationTechnologies[], ... }
  operationVolumes[]
    alias, ordinal, isBVLOS
    timeBegin, timeEnd            ISO-8601 Z   NOT effectiveTimeBegin/End
    operationGeometry
      minAltitude / maxAltitude   { altitudeValue, altitudeType, unitsOfMeasure }
      geom                        GeoJSON Polygon   NOT horizontalProjection
```

There is **no top-level `flightType`** — `modeOfOperation` is the field.
(`flightType` exists only inside `flightDetails`.)

### 7.5 Result

```
== 2. POST /avm/auth/skyzr/hello (binds x-id to the user) ==
   email=e2e-dronetest@ops.io  orgId=1  active=True
   registrationNumbers=PH-DRN-REG-AX1B2
   allowOperationPlans=True
== 4. filing plan 89eb559e-7f30-4c0b-9f41-8025ae3fdaf8 ==
   HTTP 200  {"code":1200,"message":"OK",
              "details":["Operation plan authorization request received"]}
== 5. validating ==
   plans 13 -> 14
   state      : AUTHORIZED
   authorization: GRANTED   conflicts=0   errors=0

PASS - plan 89eb559e-... is AUTHORIZED / GRANTED
== 6. withdrawing the test plan ==
   HTTP 200
```

Authorization is automatic and took ~10 s (`startTime` 09:12:40 → `endTime`
09:12:50). Post-withdraw state: `CLOSED / WITHDRAWN`.

### 7.6 Scoreboard

| Leg of the diagram | Result |
|---|---|
| Email → Keycloak DOR | **PASS** |
| Keycloak DOR → DMS webhook | **PASS** — operator row in ~10 s |
| Operator membership | **PASS** — invite `JOINED`, `pilot` + `operator_member` |
| Keycloak DOR → FLYK | **PASS** — session bound, `registrationNumbers` resolved |
| FLYK → flight plan | **PASS** — `AUTHORIZED` / `GRANTED` |
| BankID → Keycloak DOR | **API proven, ceremony not** (§3.4) |

Once Keycloak issues the session the flow is identical regardless of which IdP
authenticated the user — so §7 also covers the BankID path from step 3 onward.

---

## 8. Data flow reference

### 8.1 BankID authentication

```
Browser            Keycloak (dor-keycloak)         BankID API
   │ 1 click "BankID"      │                     appapi2.test.bankid.com
   ├──────────────────────▶│ 2 POST /rp/v6.0/auth  (mTLS, keystore.p12)
   │                       ├───────────────────────────▶│
   │                       │ 3 orderRef, autoStartToken, qrStartToken/Secret
   │ 4 QR / launch app     │◀───────────────────────────┤
   │◀──────────────────────┤                            │
   │ 5 user approves in the BankID app (PIN/biometric) ▶│
   │                       │ 6 POST /rp/v6.0/collect  (polled)
   │                       ├───────────────────────────▶│
   │                       │ 7 complete + personalNumber, name
   │ 8 Keycloak session, then OIDC code back to the app │
   │◀──────────────────────┤                            │
```

Three distinct credentials: our RP certificate (BankID authenticates *us*), the
BankID server certificate (we authenticate *them*), and the end user's own
BankID (BankID authenticates *them* — never us).

### 8.2 Registration → DMS

```
user registers / first BankID login
        │  Keycloak REGISTER event
        ▼  skyzr-dms-webhook-listener
POST https://uas.ops.io/svc-pm/api/users  ──▶ dor-be
        ▼
INSERT dor_be.users        active=1, allow_login=1, verified=0, verified_by='NONE'
INSERT dor_be.user_roles   role_id=1 default_user, org_id=NULL
```

Measured latency **≈ 10 s** (Keycloak user 04:11:08 → DMS row 04:11:18).
Confirm the listener is enabled:

```bash
curl -s -H "Authorization: Bearer $TOKEN" "$BASE/admin/realms/utm-dor" \
| python -c "import sys,json;print(json.load(sys.stdin)['eventsListeners'])"
# ['jboss-logging', 'skyzr-dms-webhook-listener']
```

### 8.3 Operator identity — three doors, no fourth

| Path | Endpoint | Requires |
|---|---|---|
| Import national registration | `POST /api/import/registration/pin` | `registrationNumber` + `registrationPin` (Transportstyrelsen) |
| Other EU country | `POST /api/organisations/createeu` | existing EU registration number |
| Join an existing operator | `POST /api/invites/accept/{id}` | invite from an `operator_administrator` |

**Nothing creates an operator from nothing.** Verified: `isRegisteringOperator:
true` on `PUT /api/users/{id}` returns 200 and flips `profileComplete`, but
creates **no** organisation.

This is why BankID matters beyond authentication: it supplies the **personal
number** that ties a natural person to a national operator registration.

Consequence for "Email Auto approval": it can auto-verify a user and auto-accept
an invite. It **cannot** conjure an operator.

### 8.4 Flight plan creation

The end user's token never reaches the plan service:

```bash
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $UTM_DOR_TOKEN" \
  https://uas.ops.io/operation-plan-service/v1/operation-plans
# 401   Bearer error="invalid_token", "Signed JWT rejected:
#       Another algorithm expected, or no matching key(s) found"
```

`operation-plan-service` trusts only `issuer-uri=.../realms/swim` with role
`partner_utm`. FLYK brokers with its own credential
(`server/utm/utm.js → getToken()`, `grant_type=client_credentials`),
server-side only:

```
Pilot ──(utm-dor user token)──▶ FLYK ──(swim client_credentials)──▶ plan service
```

**Unverified users can still fly.** `cm/flyk` sets `ALLOW_UNVERIFIED_USER=true`,
honoured in `server/auth/skyzr.js`. So `dor_be.users.verified = 0` does **not**
block flight planning — it only blocks dor-be's own portal, where every
org-scoped endpoint 403s:

```
/svc-pm/api/organisations/1      403   <-- org-scoped
/svc-pm/api/users/organisation   403
/svc-pm/api/operators            403
/svc-pm/api/assets               403
/svc-pm/api/invites              200   <-- user-scoped
/svc-pm/api/organisations        200
```

### 8.5 Reading FLYK's source when in doubt

FLYK is plain Node and readable in place — faster than guessing at its API:

```bash
kubectl -n utm-aviamaps exec $FLYK -- sh -c 'ls -1 /app/server/'
kubectl -n utm-aviamaps exec $FLYK -- sh -c \
  "grep -oE 'router\.(post|get|put)\(\s*\"[^\"]+\"' /app/server/utm/index.js | sort -u"
kubectl -n utm-aviamaps exec $FLYK -- sh -c 'sed -n "1220,1285p" /app/server/utm/index.js'
```

Useful routes: `/avm/utm/operationplans.json`, `/avm/utm/operationplan/notify`
(create), `/avm/utm/operationplan/{id}/{activate|withdraw|cancel|endflight}`,
`/avm/utm/authorization/{id}`, `/avm/auth/skyzr/{login,hello,logout}`.

---

## 9. Troubleshooting catalogue

Every one of these was hit for real.

### 9.1 BankID

| Symptom | Cause | Fix |
|---|---|---|
| BankID tile missing from the login page | IdP disabled or jar absent | §2.4, §1 |
| Probe: connect timeout / unknown host | egress or DNS blocked | check egress to `appapi2.test.bankid.com` |
| Probe: `unable to find valid certification path` | wrong/missing truststore | §3.1 |
| Probe: 401 / 403 | BankID rejected the RP certificate | check expiry (§3.1); test certs are reissued periodically |
| `keytool` password error | production cert ≠ `qwerty123` | read the password from the IdP config |

### 9.2 Keycloak image / startup

| Symptom | Cause | Fix |
|---|---|---|
| `jakarta.xml.bind.JAXBContextFactory ... not a subtype` | shaded provider bundling JAXB | §5.4(a) |
| `retrieveToken(...) not overridden` | 26.7 API change | §5.4(b) |
| `ISPN000659 ... GeneratedMarshallerBase`, exit 1 | protostream mismatch | §5.4(c) |
| Builds fine, dies at start | build-time ≠ runtime | always run `verify.sh`, not just `kc.sh build` |
| `Hostname v1 options ... still in use` | `KC_PROXY` in the ConfigMap | §6.3 |
| Pod `1/1` but not serving | no readinessProbe | §6.4 |

### 9.3 FLYK / flight plans

FLYK returns a bare **500 for every downstream failure**. The reason is always
in the log:

```bash
kubectl -n utm-aviamaps logs deploy/flyk | grep -iE 'Error fetching|MODEL_VALIDATION|TypeError'
```

| Symptom | Cause | Fix |
|---|---|---|
| 500, `Cannot destructure property 'email' of 'req.session'` | bearer token sent instead of `x-id` | §7.3 step 3 |
| 400 `Unrecognized field "flightType"` | guessed field name | use `modeOfOperation`; the error lists all 28 valid properties |
| 400 `'version' is a required element` | omitted | supply a UUID `version` |
| 401 before the service is called | `body.operator` ∉ `registrationNumbers` | fix operator membership |
| `/svc-pm/api/*` org endpoints 403 | `verified = 0` | irrelevant to FLYK; affects only the operator portal |
| `operationplans.json` returns `[]` | no session, so no operator filter | bind `x-id` first |

---

## 10. Environment traps

Collected because each cost real time.

| Trap | Symptom | Workaround |
|---|---|---|
| Keycloak pod has no `tar` | `kubectl cp` → `exec: "tar": not found` | `kubectl exec -i … -- sh -c 'cat > /tmp/x'` |
| Pod has no `curl`/`wget`/`openssl` | `/dev/tcp` also unavailable (shell is `dash`) | use `java` / `keytool`, or a debug pod |
| Git-Bash `getent` lies about DNS | reports "NO DNS" for hosts that resolve fine | use `Resolve-DnsName` or `nslookup` |
| MSYS path translation | `working directory 'C:/Program Files/Git/src' is invalid` | `export MSYS_NO_PATHCONV=1` |
| `KUBECONFIG` as a POSIX path | `dial tcp 127.0.0.1:8080` | use the Windows path |
| Docker build context absolute under MSYS | `path "/c/Users/..." not found` | `cd "$HERE" && docker build … .` |
| PowerShell 5.1 `-MaximumRedirection 0` | throws `MaximumRedirectExceeded` instead of returning the 302 | let it follow; inspect `BaseResponse.ResponseUri` |
| PowerShell function named `GM` | shadows the `Get-Member` alias → parser error | rename it |
| `curl` without `--max-time` | probe loops stall on half-open connections | always set `--max-time` |
| Harbor internal CA | `Could not establish trust relationship` | `curl -k` for read-only queries |
| Keycloak admin token ~60 s | second call 401s | fetch and use within one script |
| `bit(1)` columns | render blank under `mysql -t` | `CAST(col AS UNSIGNED)` |
| Column names move between KC versions | `Unknown column 'i.ALIAS'` | check `information_schema.COLUMNS` first |
| `core-database` pod renamed | `pods "core-database-..." not found` | re-resolve the pod name |
| OpenAPI response has a BOM | `json.load` fails | open with `encoding='utf-8-sig'` |

---

## 11. Open items

1. **BankID browser ceremony has never completed on this cluster.**
   `FEDERATED_IDENTITY` is empty for `utm-dor` (§3.4). Needs a person with a
   BankID test identity. Everything downstream is covered by §7.
2. **`sweden-connect-sandbox` is disabled.** Turned off by `admin1` on
   2026-09-24 05:50, *after* the 05:28 migration — a human action, not an
   upgrade side effect (confirmed in `ADMIN_EVENT_ENTITY`, §4.5). Re-enabling it
   is also the real test of whether sweden-connect **0.5.0** handles the
   existing SAML config — the largest unverified delta of the upgrade.
3. **A synthetic plan is still `AUTHORIZED`:**
   `157228d7-c325-4481-be86-6495ece9b7b6`, window 09:57–10:17Z 2026-09-24
   (elapsed). Retire with `POST /avm/utm/operationplan/{id}/withdraw` + `x-id`.
4. **Test account left in place:** `e2e-dronetest@ops.io`, `dor_be.users` id 12,
   org 1, `pilot` + `operator_member`, `verified = 0`. Keep as a regression
   account or remove.
5. **`verifyEmail` was set to `false`** on `utm-dor` to resolve a contradiction
   (flag on, required-action provider disabled ⇒ users stamped with an action
   that could never run). Prior config: `_utm-dor-realm-backup.json`.
6. **"Email Auto approval" is half implemented** — auto-create yes, operator
   membership no (§8.3).
7. **`cm/dor-keycloak` still carries Hostname v1 options** and the deployment
   still has **no readinessProbe** (§6.3, §6.4).
8. `basic-storage-provider-1.0.jar` and `apple-social-identity-provider-1.0.2.jar`
   are vendor binaries with no local source; they register on 26.7.3, but
   untested paths remain untested.
9. bankid4keycloak has **no upstream release targeting 26.7.3**; this build uses
   master (`34a5932`) plus the two fixes in §5.4.

---

## 12. Artifacts

| Path | Purpose |
|---|---|
| `e2e/flyk-file-flight-plan.ps1` | the §7 E2E — login, session, file a plan, validate, optionally withdraw |
| `e2e/BankidProbe.java` | mTLS probe of the BankID API (§3.2) |
| `keycloak-skyzr-26/Dockerfile` | the 26.7.3 image |
| `keycloak-skyzr-26/build.sh` | build + sanity gate; offline-aware (§5.2) |
| `keycloak-skyzr-26/verify.sh` | 15 checks against a live container (§5.7) |
| `keycloak-skyzr-26/bankid-src/` | bankid4keycloak with the two fixes (§5.4 b, c) |
| `keycloak-skyzr-26/swedenconnect-src/` | sweden-connect v0.5.0 source (§5.4 a) |
| `_utm-dor-realm-backup.json` | realm config before the `verifyEmail` change |
