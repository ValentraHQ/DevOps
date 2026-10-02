# UTM Platform Documentation

Documentation for the UTM observability and identity work, in both Markdown (repository) and
Confluence storage format (wiki).

## Documents

| Document | Markdown | Confluence | Audience |
|---|---|---|---|
| **Observability & Remediation** — the full record: findings, fixes, validation, gaps, runbooks | [`UTM-Observability-Documentation.md`](UTM-Observability-Documentation.md) | `UTM-Observability-Documentation.confluence.xml` | Platform / SRE / engineering leadership |
| **Technical Reference** — dashboard suite build detail, queries, file manifest | [`UTM-Observability-Technical-Reference.md`](UTM-Observability-Technical-Reference.md) | `UTM-Observability-Technical-Reference.confluence.xml` | Engineers maintaining the dashboards |
| **BankID ↔ Keycloak (SKYZR/DOR)** — architecture, data flow and **reproducible E2E**: BankID mTLS, DMS propagation, operator identity, FLYK flight-plan filing, the 26.7.3 image | [`BankID-Keycloak-SKYZR-Integration.md`](BankID-Keycloak-SKYZR-Integration.md) | `BankID-Keycloak-SKYZR-Integration.confluence.xml` | Identity / integration engineers |
| **Terrain Data Model** — know-how for `Terrain_Data_Model.zip` → LFV DTM: the script, how it works, VRT/COG assembly, verified against known-good acceptance values | [`Terrain-Data-Model-Know-How.md`](Terrain-Data-Model-Know-How.md) | `Terrain-Data-Model-Know-How.confluence.xml` | GIS / terrain-data engineers |
| **UTM System Architecture** — namespace/subsystem map, per-service purpose (confidence-tagged), data flow, messaging backbone, data stores | [`UTM-System-Architecture.md`](UTM-System-Architecture.md) | `UTM-System-Architecture.confluence.xml` | Engineers new to the platform; anyone tracing a request across services |

Suggested Confluence page tree:

```
UTM Platform
├── Observability
│   ├── Observability & Remediation      ← start here
│   ├── Technical Reference
│   └── Runbooks                          (section 12 of the main document)
└── Identity
    └── BankID ↔ Keycloak (SKYZR/DOR)     ← supersedes "BankID-Keycloak Integration v1"
```

---

## Publishing to Confluence

The `.confluence.xml` files are **Confluence storage format** (XHTML plus
Atlassian `ac:` macro tags). They are not Markdown and not plain HTML.

### Option 1 — REST API (recommended, reproducible)

```bash
CONF="https://<your-site>.atlassian.net/wiki"
SPACE="UTM"
TITLE="Observability & Remediation"
BODY=$(python - <<'PY'
import json
print(json.dumps(open("UTM-Observability-Documentation.confluence.xml",
                      encoding="utf-8").read()))
PY
)

curl -u "$USER:$API_TOKEN" -X POST "$CONF/rest/api/content" \
  -H 'Content-Type: application/json' \
  -d "{
        \"type\": \"page\",
        \"title\": \"$TITLE\",
        \"space\": {\"key\": \"$SPACE\"},
        \"body\": {\"storage\": {\"value\": $BODY, \"representation\": \"storage\"}}
      }"
```

To **update** an existing page, `PUT` to `/rest/api/content/{id}` and increment
`version.number`.

### Option 2 — Confluence UI

1. Create or open the page.
2. **⋯ → Advanced → Source editor** (Cloud) or **Edit → Source** (Server/DC).
3. Paste the file contents and save.

> If your Confluence has the source editor disabled, ask a space admin to enable
> it, or use the REST API above. Pasting the rendered Markdown into the normal
> editor mostly works but loses the code-block languages, panel macros and the
> table-of-contents macro.

---

## Regenerating the Confluence files

**Markdown is the single source of truth.** After editing any `.md`, regenerate:

```bash
cd docs
python md2confluence.py UTM-Observability-Documentation.md \
                        UTM-Observability-Documentation.confluence.xml
python md2confluence.py UTM-Observability-Technical-Reference.md \
                        UTM-Observability-Technical-Reference.confluence.xml
```

`md2confluence.py` converts headings, tables, fenced code blocks (→ `code`
macro with language), blockquotes (→ `info` / `warning` / `note` panel macros),
lists, inline formatting and links, and injects a `toc` macro after the title.

Validate well-formedness before publishing:

```bash
python - <<'PY'
import xml.etree.ElementTree as ET
s = open("UTM-Observability-Documentation.confluence.xml", encoding="utf-8").read()
ET.fromstring('<root xmlns:ac="http://atlassian.com/content" '
              'xmlns:ri="http://atlassian.com/resource/identifier">' + s + '</root>')
print("WELL-FORMED")
PY
```

---

## Keeping the technical reference in sync

`UTM-Observability-Technical-Reference.md` is a copy of
`../observability/README.md`, which sits next to the dashboard JSON it
describes. When that file changes, refresh the copy and regenerate:

```bash
cp ../observability/README.md UTM-Observability-Technical-Reference.md
python md2confluence.py UTM-Observability-Technical-Reference.md \
                        UTM-Observability-Technical-Reference.confluence.xml
```

---

## ⚠️ Read before acting on these documents

Everything described was applied to the **live cluster** and is **not yet in
Git**. ArgoCD application `utm-statistics` has `selfHeal: true` and is currently
down; when restored it will revert every ConfigMap and Deployment change.

See **section 11** of the main document for the full commit checklist.
