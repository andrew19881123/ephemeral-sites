# ephemeral-sites — Specifica Tecnica Definitiva

**Versione:** 1.0 (finale)
**Data:** 2026-05-06
**Stato:** ✅ Approvata per implementazione
**Destinatario:** Team di sviluppo
**Stima effort:** 8-12 giorni/persona

---

## Indice

1. [Executive Summary](#1-executive-summary)
2. [Use Cases](#2-use-cases)
3. [Decisioni Architetturali](#3-decisioni-architetturali)
4. [Architettura di Sistema](#4-architettura-di-sistema)
5. [API Specification](#5-api-specification)
6. [Data Model](#6-data-model)
7. [Sicurezza](#7-sicurezza)
8. [DNS & TLS](#8-dns--tls)
9. [Configurazione (values.yaml)](#9-configurazione-valuesyaml)
10. [Struttura Repository](#10-struttura-repository)
11. [Testing Strategy](#11-testing-strategy)
12. [Deployment & Operations](#12-deployment--operations)
13. [Open Points](#13-open-points)
14. [Non-Goals](#14-non-goals)
15. [Definition of Done](#15-definition-of-done)
16. [Roadmap & Ordine di Implementazione](#16-roadmap--ordine-di-implementazione)

---

## 1. Executive Summary

### 1.1 Descrizione

`ephemeral-sites` è un servizio self-hosted **monoutente** per Kubernetes (target: k3s su GCP) che espone un'API REST per pubblicare temporaneamente Single Page Application (SPA) statiche a partire da un archivio ZIP. Un'unica persona (l'owner) usa il servizio per esperimenti personali, prototipi, demo e portfolio. Gli URL generati sono **pubblici su Internet** e condivisibili con chiunque.

### 1.2 User Story principale

> Come sviluppatore, lavorando ai miei esperimenti, voglio inviare uno ZIP della mia SPA a un'API e ottenere un URL pubblico temporaneo, così da condividere rapidamente prototipi senza riconfigurare deployment ogni volta.

```bash
curl -X PUT https://api.preview.miei-esperimenti.dev/api/v1/sites/color-picker-demo \
  -H "Authorization: Bearer $API_KEY" \
  -F "file=@dist.zip" \
  -F "ttl_seconds=2592000"   # 30 giorni

# Response:
# { "url": "https://color-picker-demo.preview.miei-esperimenti.dev", ... }
```

### 1.3 Profilo d'uso atteso

- **Volume**: 1-20 deploy/giorno, picchi di 50 in giorni di esperimento intenso
- **Concorrenza**: praticamente nessuna (un solo client attivo)
- **Siti simultanei**: da 5 a 100, con TTL misti (da 1h a permanenti)
- **Dimensione tipica**: 1-20 MB (SPA buildate), occasionali 100-300 MB con asset
- **Traffico**: pochi accessi per sito (condivisioni puntuali)

### 1.4 Minacce rilevanti

1. API key leaked → abuso deploy massivo → riempimento storage
2. URL pubblico indovinato → accesso non voluto a WIP
3. Zip bomb malevoli se l'API key trapela
4. Scraping bot dei siti pubblicati
5. Siti usati come vettore phishing/malware (reputazione dominio)

---

## 2. Use Cases

| ID | Use Case | Note di design |
|----|----------|----------------|
| UC-A | **Esperimento quick & dirty** — deploy + share Twitter/forum, TTL 24-48h | Slug custom memorabili |
| UC-B | **Prototipo iterativo** — re-deploy stesso slug N volte durante sviluppo | Upsert `PUT`, overwrite atomico senza 404 |
| UC-C | **Demo persistente** — esperimento tenuto online mesi (portfolio) | TTL lungo o permanente (`ttl=-1`) |
| UC-D | **A/B di due versioni** — `exp-v1` e `exp-v2` per comparison | Slug paralleli |
| UC-E | **Landing page per evento** — scade dopo talk/meetup | TTL 30-90gg |
| UC-F | **Archivio esperimenti passati** — rivedere vecchi prototipi | Permanenti + anti-indexing |
| UC-G | **Deploy da CI** — side project con GitHub Actions che deploya main | Automazione, upsert |
| UC-H | **Config runtime per SPA** — stessa SPA con API URL diversi | Iniezione `/config.json` |
| UC-I | **Protezione link privato** — condivisione a poche persone fidate | Password basic-auth |
| UC-J | **Evitare indicizzazione Google** — esperimenti WIP non in SERP | `X-Robots-Tag: noindex` di default |

---

## 3. Decisioni Architetturali

### 3.1 Stack tecnologico

| Area | Decisione | Rationale |
|------|-----------|-----------|
| Linguaggio | Python 3.12 | Coerenza con ecosistema dev del repo principale |
| Web framework | FastAPI | Async, OpenAPI auto-gen, Pydantic validation |
| Database | SQLite con WAL mode | Single user, I/O basso, backup = file singolo |
| Storage | PVC RWO (default `local-path`) | Semplicità, zero deps esotiche |
| Container base | `python:3.12-slim` hardened | Footprint minimo, security |
| Test framework | pytest + pytest-asyncio | Standard Python |

### 3.2 Infrastruttura

| Area | Decisione |
|------|-----------|
| Kubernetes | k3s su GCP (VM con IP statico) |
| Ingress | Traefik (default k3s) |
| DNS | Cloudflare (piano Free) |
| TLS | Let's Encrypt wildcard via cert-manager + DNS-01 |
| Registry immagini | GitHub Container Registry (`ghcr.io`) |
| Licenza | Apache-2.0 |

### 3.3 Pattern architetturali

| Scelta | Valore | Rationale |
|--------|--------|-----------|
| Deployment mode | **Monolith** (API + server statico stesso pod) + CronJob cleanup separato | Evita RWX, sufficiente per single user |
| Repliche | `replicas: 1` fisso | Single user, no HA critico |
| Endpoint primario | `PUT /api/v1/sites/{slug}` upsert | UC-B e UC-G sono i più frequenti |
| Overwrite atomico | Swap directory con `flock`, finestra <1ms | Zero 404 intermedi durante re-deploy |
| Anti-indexing default | `X-Robots-Tag: noindex, nofollow` | Esperimenti non devono finire su Google |
| TTL permanente | Supportato (`ttl_seconds: -1`) | UC-C, UC-F |
| Quota globale storage | Check pre-upload vs `maxTotalStorageBytes` | Anti-abuse key leaked |
| Rate limit | 60/min per API key | Generoso per single user, stop abuser |

### 3.4 Scelte esplicitamente scartate

- ❌ Multi-tenancy / workspace / RBAC complesso
- ❌ Webhook (single user se ne accorge da sé)
- ❌ UI web (CLI + API sufficienti in v1)
- ❌ Git integration (fuori scope)
- ❌ OpenTelemetry tracing
- ❌ Notifiche di scadenza
- ❌ SSR / runtime Node.js (solo static serve)
- ❌ Build step integrato

---

## 4. Architettura di Sistema

### 4.1 Diagramma componenti

```
                           ┌──────────────────────────────────────┐
                           │  DNS Cloudflare:                     │
                           │   *.preview.miei-esperimenti.dev  →  │
                           │   api.preview.miei-esperimenti.dev → │ IP statico GCP
                           └─────────────────┬────────────────────┘
                                             │
                                     ┌───────▼─────────┐
                                     │  Traefik         │
                                     │  (k3s default)   │
                                     │  + wildcard TLS  │
                                     └───────┬──────────┘
                                             │
                            ┌────────────────┼─────────────────┐
                            │                                  │
                     host: api.preview              host: *.preview
                            │                                  │
                            ▼                                  ▼
                   ┌────────────────────────────────────────────┐
                   │  ephemeral-sites-app (Deployment, 1 replica)│
                   │  ┌──────────────┐   ┌──────────────────┐   │
                   │  │ /api/v1/*    │   │ static server     │   │
                   │  │ (port 8080)  │   │ (port 8081)       │   │
                   │  └──────┬───────┘   └────────┬──────────┘   │
                   │         │                    │              │
                   │         └──────┬─────────────┘              │
                   │                │                            │
                   │        ┌───────▼────────┐                   │
                   │        │ /data (PVC RWO)│                   │
                   │        │  ├─ sites/     │                   │
                   │        │  ├─ db/        │                   │
                   │        │  └─ tmp/       │                   │
                   │        └────────────────┘                   │
                   └────────────────────────────────────────────┘
                                    │
                     ┌──────────────▼─────────────┐
                     │ ephemeral-sites-cleanup    │
                     │ CronJob */5 * * * *        │
                     │ (monta stesso PVC)         │
                     └────────────────────────────┘
```

### 4.2 Flusso PUT /api/v1/sites/{slug} (upsert)

```
1. Auth middleware: valida Bearer token
2. Rate limit: check bucket per API key (60/min)
3. Quota check: somma(active_sites_size) + stima(zip_decompresso) < maxTotalStorageBytes
4. Validator ZIP:
   - path traversal / symlink / absolute paths → reject 400
   - zip bomb (ratio, size cap decompresso) → reject 400
   - file count > limit → reject 400
   - extension whitelist check → reject 400
   - index.html al root (o singola cartella top-level da appiattire)
5. Generate/validate slug
6. Estrazione atomica:
   - mkdir /data/sites/{slug}.new/
   - extract zip in {slug}.new/
   - se runtime_config presente: write {slug}.new/config.json
7. Swap atomico (con flock):
   - if exists /data/sites/{slug}: mv → {slug}.old
   - mv {slug}.new → {slug}
   - rm -rf {slug}.old
8. DB transaction:
   - UPSERT sites (INSERT ... ON CONFLICT UPDATE)
   - INSERT event_log (created | replaced)
9. Return JSON con URL, expires_at, delete_token
```

### 4.3 Flusso GET static content (server)

```
1. Parse subdomain da header Host → slug
2. Cache lookup in-memory (TTL 60s) {slug → metadata}
3. Se cache miss: SELECT * FROM sites WHERE slug=? AND (expires_at IS NULL OR expires_at > NOW())
4. Se non trovato o scaduto → 404
5. Se password_hash NOT NULL: verify basic auth → 401 altrimenti
6. Resolve path fisico: /data/sites/{slug}/{url_path} (normalize, reject '..')
7. Se file esiste:
   - Serve con Content-Type corretto
   - Apply security headers (noindex default, CSP, X-Frame-Options...)
   - INCR hits (async batch ogni 10 accessi per ridurre I/O DB)
8. Se file NON esiste:
   - Se spa_mode=true AND url_path non matcha pattern asset (/static/, estensioni note):
     → serve index.html con status 200
   - Altrimenti: serve 404.html custom se presente, fallback 404
```

### 4.4 Flusso Cleanup (CronJob)

```
Ogni 5 minuti:
1. SELECT slug, path FROM sites WHERE expires_at IS NOT NULL AND expires_at < NOW()
2. Per ogni riga:
   - flock /data/sites/.{slug}.lock
   - rm -rf /data/sites/{slug}
   - DELETE FROM sites WHERE slug=?
   - INSERT event_log (slug, event='expired')
   - emit metric sites_expired_total
3. VACUUM SQLite se sites count è diminuito significativamente
4. Log INFO solo se almeno 1 sito cancellato (altrimenti DEBUG)
5. Exit 0
```

---

## 5. API Specification

### 5.1 Autenticazione

**Header**: `Authorization: Bearer <API_KEY>`

API keys caricate all'avvio dal Secret `ephemeral-sites-auth`:
```
API_KEYS="main:plainkey_xxxxx,ci:plainkey_yyyyy"
```

Nomi liberi (usati solo per log). Tutte le keys hanno stesso potere (no admin/non-admin in v1).

**Errori auth:**
- 401 Unauthorized: header mancante/malformato
- 403 Forbidden: key valida ma `disabled=true`

### 5.2 Endpoint: PUT /api/v1/sites/{slug} ⭐

Upsert (create or replace). **Endpoint principale** — ottimizzato per UC-B (prototipazione iterativa) e UC-G (CI/CD).

**Path param:**
- `slug`: regex `^[a-z0-9][a-z0-9-]{2,62}$`

**Request body** (`multipart/form-data`):

| Campo | Tipo | Req | Default | Range/Note |
|-------|------|-----|---------|------------|
| `file` | file .zip | ✅ | — | Max 500 MiB (configurabile) |
| `ttl_seconds` | int | ❌ | 86400 | `-1` = permanente, altrimenti 60 .. 31536000 |
| `password` | string | ❌ | null | Se presente → basic auth |
| `spa_mode` | bool | ❌ | true | Fallback SPA su 404 |
| `runtime_config` | string (JSON) | ❌ | null | Iniettato come `/config.json` |
| `allow_indexing` | bool | ❌ | false | Se false: `X-Robots-Tag: noindex, nofollow, noarchive` |
| `labels` | string (JSON) | ❌ | null | Array di stringhe, per filtraggio futuro |

**Response 200** (create o replace — idempotente):
```json
{
  "slug": "color-picker-demo",
  "url": "https://color-picker-demo.preview.miei-esperimenti.dev",
  "created_at": "2026-05-06T21:00:00Z",
  "updated_at": "2026-05-06T21:30:00Z",
  "expires_at": "2026-06-05T21:30:00Z",
  "size_bytes": 2457600,
  "files_count": 42,
  "delete_token": "dt_abc123xyz",
  "spa_mode": true,
  "password_protected": false,
  "allow_indexing": false,
  "labels": ["experiment", "ml"]
}
```

Nota: `expires_at` è `null` se `ttl_seconds=-1`.

**Error codes:**
- 400: validazione (zip corrotto/bomb, slug invalido, ttl fuori range, JSON malformato)
- 401: auth mancante/invalida
- 403: key disabilitata
- 413 Payload Too Large: zip > `maxZipSize`
- 429 Too Many Requests: rate limit
- 507 Insufficient Storage: quota globale esaurita

### 5.3 Endpoint: POST /api/v1/sites

Come PUT ma **senza slug nel path**: il server genera uno slug auto `{adjective}-{noun}-{4hex}` (es. `happy-fox-a3f2`).

**Response 201 Created** con stesso payload di PUT.

Collisione: retry automatico fino a 5 volte, poi 500.

### 5.4 Endpoint: GET /api/v1/sites/{slug}

Ritorna metadata (stesso payload di PUT) + `hits` + `last_hit`.

Errors: 401, 404.

### 5.5 Endpoint: DELETE /api/v1/sites/{slug}

Auth alternativa:
- `Authorization: Bearer <API_KEY>`, **oppure**
- `X-Delete-Token: <token>` (ritornato al deploy)

**Response**: 204 No Content.

Effetti:
- `rm -rf /data/sites/{slug}` (con flock)
- `DELETE FROM sites WHERE slug=?`
- `INSERT event_log (event='deleted', metadata={reason: 'manual'|'token'})`

### 5.6 Endpoint: PATCH /api/v1/sites/{slug}

Aggiorna metadata senza ri-uploadare il file.

**Body** (`application/json`), tutti opzionali:
```json
{
  "ttl_seconds": 2592000,
  "password": "newpass",        // null per rimuovere
  "allow_indexing": true,
  "labels": ["portfolio"]
}
```

Semantica TTL: `new_expires_at = NOW() + ttl_seconds` (non additivo sul vecchio).

**Response 200**: payload completo.

### 5.7 Endpoint: GET /api/v1/sites

Lista paginata siti attivi.

**Query params:**
- `label=experiment` (opz, filtra per label nell'array)
- `limit=50` (default 50, max 200)
- `offset=0`
- `sort=-created_at` (campo; `-` per descending)

**Response 200:**
```json
{
  "total": 127,
  "items": [ {...}, {...} ]
}
```

`delete_token_hash` e `password_hash` **mai** restituiti.

### 5.8 Endpoint: probes & metrics

- `GET /healthz` → 200 sempre se processo vivo (liveness)
- `GET /readyz` → 200 se DB aperto e `/data/sites` scrivibile (readiness)
- `GET /metrics` → Prometheus plaintext

**Metriche esposte:**
```
ephemeral_sites_total                         gauge
ephemeral_sites_created_total                 counter (label: api_key_name)
ephemeral_sites_replaced_total                counter (label: api_key_name)
ephemeral_sites_expired_total                 counter
ephemeral_sites_deleted_total                 counter (label: reason)
ephemeral_sites_storage_bytes                 gauge
ephemeral_sites_http_requests_total           counter (labels: method, endpoint, status)
ephemeral_sites_http_request_duration_seconds histogram (label: endpoint)
ephemeral_sites_quota_reject_total            counter
ephemeral_sites_rate_limit_hit_total          counter (label: api_key_name)
```

### 5.9 Endpoint pubblici sui siti serviti

Nessuna API, solo file statici. **Due path speciali** automaticamente iniettati:

- `/_ephemeral/info` → JSON `{slug, expires_at, hits}` (utile per countdown UI)
- `/config.json` → disponibile solo se `runtime_config` era presente al deploy

Entrambi serviti con `Cache-Control: no-cache`.

---

## 6. Data Model

### 6.1 Schema SQLite

```sql
-- Pragma obbligatori all'inizializzazione
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
PRAGMA busy_timeout=5000;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS sites (
  slug              TEXT PRIMARY KEY,
  path              TEXT NOT NULL,
  created_at        TEXT NOT NULL,     -- ISO 8601 UTC
  updated_at        TEXT NOT NULL,
  expires_at        TEXT,              -- NULL = permanente
  size_bytes        INTEGER NOT NULL,
  files_count       INTEGER NOT NULL,
  password_hash     TEXT,              -- bcrypt
  delete_token_hash TEXT NOT NULL,     -- bcrypt
  spa_mode          INTEGER NOT NULL DEFAULT 1,
  allow_indexing    INTEGER NOT NULL DEFAULT 0,
  hits              INTEGER NOT NULL DEFAULT 0,
  last_hit          TEXT,
  created_by        TEXT NOT NULL,
  labels            TEXT,              -- JSON array
  runtime_config    TEXT               -- JSON blob archiviato per re-serve
);

CREATE INDEX idx_sites_expires ON sites(expires_at) WHERE expires_at IS NOT NULL;
CREATE INDEX idx_sites_created ON sites(created_at);

CREATE TABLE IF NOT EXISTS api_keys (
  name        TEXT PRIMARY KEY,
  key_hash    TEXT NOT NULL,           -- bcrypt
  created_at  TEXT NOT NULL,
  last_used   TEXT,
  disabled    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS event_log (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  slug        TEXT NOT NULL,
  event       TEXT NOT NULL,           -- created|replaced|extended|deleted|expired
  timestamp   TEXT NOT NULL,
  api_key     TEXT,
  metadata    TEXT                     -- JSON
);

CREATE INDEX idx_event_log_slug ON event_log(slug);
CREATE INDEX idx_event_log_ts ON event_log(timestamp);

-- Schema versioning
PRAGMA user_version = 1;
```

**Migrations**: gestite via `PRAGMA user_version` + script Python idempotenti eseguiti all'avvio app. Backup automatico del DB prima di migration `v{N}` → `ephemeral-sites.db.backup-v{N-1}`.

### 6.2 Filesystem layout

```
/data/
├── db/
│   ├── ephemeral-sites.db
│   ├── ephemeral-sites.db-wal
│   ├── ephemeral-sites.db-shm
│   └── ephemeral-sites.db.backup-v0    # Backup migration pre-v1
├── sites/
│   ├── .lock/                           # File lock per swap atomici
│   │   └── {slug}.lock
│   ├── color-picker-demo/
│   │   ├── index.html
│   │   ├── config.json                  # Se runtime_config era presente
│   │   └── static/...
│   └── experiment-42/...
└── tmp/
    └── <uuid>/                          # Staging uploads (cleanup su fail)
```

### 6.3 Event log retention

Il cleanup CronJob, oltre alla rimozione siti scaduti, esegue settimanalmente:
```sql
DELETE FROM event_log WHERE timestamp < datetime('now', '-90 days');
```

Mantiene storico 90gg per audit, poi purge.

---

## 7. Sicurezza

### 7.1 Validazione ZIP (critica)

Implementata in `src/ephemeral_sites/validator.py`.

**Rejection rules** (ognuna = 400 Bad Request):

1. **Path traversal**: entry con `..`, leading `/`, segmenti vuoti, drive letters Windows (`C:\`)
2. **Symlinks**: `zinfo.external_attr >> 16` indica symbolic link → reject
3. **Zip bomb**:
   - Compression ratio globale < 0.01 (1:100) → reject
   - Totale decompresso > `maxZipSize * 10` → reject
   - Singolo file decompresso > `maxZipSize * 2` → reject
4. **File count**: > `maxFilesPerSite` → reject
5. **Extension whitelist** (configurabile): se una entry ha estensione non-whitelisted → reject intero zip
6. **Required**: `index.html` al root (o dentro UNICA cartella top-level, in tal caso il contenuto viene appiattito dal validator)

**Testing obbligatorio** per v1:
- Test con zip bomb reale (42.zip)
- Test con payload traversal (`../../etc/passwd`)
- Test con symlink a `/etc/shadow`
- Test con 10.000 file dentro zip
- Test con singolo file da 10 GB (quindi decompresso > size*10)

### 7.2 Security headers sui siti serviti

Applicati automaticamente dal server statico:

```http
X-Content-Type-Options: nosniff
X-Frame-Options: SAMEORIGIN
Referrer-Policy: strict-origin-when-cross-origin
Content-Security-Policy: default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; font-src 'self' data:; connect-src *;
X-Robots-Tag: noindex, nofollow, noarchive       # Solo se allow_indexing=false
Cache-Control: public, max-age=300                # Per asset statici
Cache-Control: no-cache                           # Per index.html, config.json, _ephemeral/*
```

Nota `connect-src *`: le SPA frequentemente chiamano API esterne; è il pragmatismo richiesto dal caso d'uso.

### 7.3 Container hardening

```yaml
securityContext:
  runAsNonRoot: true
  runAsUser: 10001
  runAsGroup: 10001
  fsGroup: 10001
  allowPrivilegeEscalation: false
  capabilities:
    drop: ["ALL"]
  readOnlyRootFilesystem: true
  seccompProfile:
    type: RuntimeDefault
volumes:
  - name: tmp
    emptyDir: {}
  - name: data
    persistentVolumeClaim:
      claimName: ephemeral-sites-data
volumeMounts:
  - name: tmp
    mountPath: /tmp
  - name: data
    mountPath: /data
```

### 7.4 Rate limiting

- Default: 60 req/min per API key sugli endpoint PUT/POST/PATCH/DELETE
- Implementazione: token bucket in-memory (sufficiente per `replicas: 1`)
- `GET /api/v1/sites/{slug}`: non rate-limitato
- Accesso pubblico ai siti statici: non rate-limitato (cache Traefik assorbe)

### 7.5 Rotazione API key (procedura)

1. Genera nuova key: `NEW=$(openssl rand -hex 32)`
2. Aggiungi al Secret: `API_KEYS="main:OLD_KEY,main-next:NEW_KEY"`
3. Rolling restart Deployment: `kubectl rollout restart deploy/ephemeral-sites -n ephemeral-sites`
4. Switch client alla nuova key, verifica funziona
5. Rimuovi OLD dal Secret: `API_KEYS="main:NEW_KEY"` (rinominando a `main`)
6. Rolling restart

Zero downtime garantito.

### 7.6 Secrets handling

- **MAI loggare** API key in chiaro, `delete_token`, `password`
- **MAI esporre** path filesystem assoluti in error response
- Error generici in prod: `{"error": "invalid_request", "detail": "Bad request"}` con `X-Request-ID` per correlazione log
- Hash bcrypt per tutto (cost=12)

---

## 8. DNS & TLS

Sezione dettagliata: tutta la configurazione per far funzionare HTTPS wildcard.

### 8.1 Setup una tantum (responsabilità ops)

**Step 1 — Dominio**:
- Registrare su Porkbun (raccomandato) il dominio scelto (es. `miei-esperimenti.dev`)
- Attivare WHOIS privacy + domain lock + auto-renewal

**Step 2 — Cloudflare**:
- Creare account free → Add Site → piano Free
- Ottenere i 2 nameserver assegnati (es. `aria.ns.cloudflare.com`, `cole.ns.cloudflare.com`)
- Sul registrar: sostituire nameserver → attendere propagazione (solitamente <2h)

**Step 3 — Record DNS**:
```
Type: A
Name: preview
Content: <IP_STATICO_GCP>
Proxy status: DNS only (grigio, NON arancione)

Type: A
Name: *.preview
Content: <IP_STATICO_GCP>
Proxy status: DNS only (grigio, NON arancione)
```

**CRITICO**: proxy Cloudflare **disattivato**. Con proxy attivo il DNS-01 challenge si complica e il TLS end-to-end richiederebbe piano "Full (strict)".

Verifica:
```bash
dig A test.preview.miei-esperimenti.dev +short
# Deve ritornare IP GCP diretto
```

**Step 4 — API Token Cloudflare**:
- Cloudflare → My Profile → API Tokens → Create Token
- Template: **Edit zone DNS**
- Permissions: `Zone:DNS:Edit`, `Zone:Zone:Read`
- Zone Resources: **Include - Specific zone - miei-esperimenti.dev**
- Salvare token (non più mostrato dopo)

### 8.2 cert-manager

**Installazione** (se non già presente):
```bash
kubectl create namespace cert-manager
helm repo add jetstack https://charts.jetstack.io
helm repo update
helm install cert-manager jetstack/cert-manager \
  --namespace cert-manager \
  --version v1.16.2 \
  --set crds.enabled=true
```

**Secret Cloudflare** (nel namespace `cert-manager`):
```bash
kubectl -n cert-manager create secret generic cloudflare-api-token \
  --from-literal=api-token='<TOKEN>'
```

**ClusterIssuer staging** (obbligatorio testare prima in staging):
```yaml
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: letsencrypt-staging
spec:
  acme:
    server: https://acme-staging-v02.api.letsencrypt.org/directory
    email: OWNER_EMAIL@example.com
    privateKeySecretRef:
      name: letsencrypt-staging-account-key
    solvers:
      - dns01:
          cloudflare:
            apiTokenSecretRef:
              name: cloudflare-api-token
              key: api-token
        selector:
          dnsZones:
            - "miei-esperimenti.dev"
```

**ClusterIssuer production** (identico a staging ma con `server: https://acme-v02.api.letsencrypt.org/directory` e nomi cambiati a `letsencrypt-prod`).

### 8.3 Certificate wildcard

Gestito dal Helm chart (`templates/certificate.yaml`):
```yaml
{{- if and .Values.ingress.tls.enabled (eq .Values.ingress.tls.mode "cert-manager") }}
apiVersion: cert-manager.io/v1
kind: Certificate
metadata:
  name: {{ include "ephemeral-sites.fullname" . }}-wildcard
  namespace: {{ .Release.Namespace }}
spec:
  secretName: {{ include "ephemeral-sites.fullname" . }}-wildcard-tls
  issuerRef:
    name: {{ .Values.ingress.tls.certManager.clusterIssuer }}
    kind: ClusterIssuer
  dnsNames:
    - {{ .Values.domain | quote }}
    - {{ .Values.wildcardHost | quote }}
    - {{ .Values.apiHost | quote }}
  renewBefore: 720h
  privateKey:
    rotationPolicy: Always
{{- end }}
```

### 8.4 Rate limits Let's Encrypt

| Limite | Valore | Impatto |
|--------|--------|---------|
| Certificates per Registered Domain | 50/settimana | Irrilevante (1 wildcard ogni 60gg) |
| Duplicate Certificate | 5/settimana | Irrilevante |
| Failed Validations | 5/ora per account/hostname | **Attenzione setup iniziale** |
| New Orders | 300/3h | Irrilevante |

**Regola d'oro**: sempre staging prima, poi switch a prod solo quando verificato.

### 8.5 Rinnovo automatico

cert-manager rinnova 30 giorni prima della scadenza (cert valido 90gg → rinnovo ogni 60gg). Zero intervento manuale.

Alert suggerito (Prometheus rule):
```yaml
- alert: CertificateExpiringSoon
  expr: (certmanager_certificate_expiration_timestamp_seconds - time()) < 14 * 24 * 3600
  labels:
    severity: warning
```

---

## 9. Configurazione (values.yaml)

Template completo — `charts/ephemeral-sites/values.yaml`:

```yaml
# ===================================================
# ephemeral-sites — values.yaml
# ===================================================

# ---- Domain & Ingress ----
domain: preview.miei-esperimenti.dev                # ⚠️ SOSTITUIRE
wildcardHost: "*.preview.miei-esperimenti.dev"      # ⚠️ SOSTITUIRE
apiHost: api.preview.miei-esperimenti.dev           # ⚠️ SOSTITUIRE

ingress:
  enabled: true
  className: traefik
  annotations: {}
  tls:
    enabled: true
    mode: cert-manager                              # cert-manager | existing-secret | disabled
    certManager:
      clusterIssuer: letsencrypt-prod
    existingSecret: ""

# ---- DNS (documentale, la config vera è nel ClusterIssuer) ----
dns:
  provider: cloudflare
  zone: miei-esperimenti.dev                        # ⚠️ SOSTITUIRE

# ---- Storage ----
storage:
  storageClass: local-path
  size: 50Gi
  accessMode: ReadWriteOnce

# ---- Limits ----
limits:
  maxZipSize: 500Mi
  maxFilesPerSite: 5000
  defaultTtlSeconds: 86400                          # 1 giorno
  maxTtlSeconds: 31536000                           # 1 anno
  allowPermanent: true                              # consente ttl=-1
  ratePerMinute: 60
  maxTotalStorageBytes: 42949672960                 # 40 GiB (80% di 50Gi)
  maxDecompressionRatio: 100
  allowedExtensions:
    - .html
    - .htm
    - .css
    - .js
    - .mjs
    - .json
    - .map
    - .xml
    - .txt
    - .png
    - .jpg
    - .jpeg
    - .gif
    - .svg
    - .webp
    - .avif
    - .ico
    - .woff
    - .woff2
    - .ttf
    - .otf
    - .eot
    - .pdf
    - .mp4
    - .webm
    - .mp3
    - .wav
    - .wasm

# ---- Defaults per siti pubblicati ----
siteDefaults:
  allowIndexing: false
  spaMode: true

# ---- Cleanup ----
cleanup:
  schedule: "*/5 * * * *"
  eventLogRetentionDays: 90
  resources:
    limits: { cpu: 200m, memory: 128Mi }
    requests: { cpu: 20m, memory: 32Mi }

# ---- App ----
app:
  replicas: 1
  image:
    repository: ghcr.io/OWNER/ephemeral-sites
    tag: "1.0.0"
    pullPolicy: IfNotPresent
  resources:
    limits: { cpu: 1000m, memory: 512Mi }
    requests: { cpu: 100m, memory: 128Mi }
  podAnnotations: {}
  nodeSelector: {}
  tolerations: []
  affinity: {}

# ---- Auth ----
auth:
  existingSecret: "ephemeral-sites-auth"

# ---- Observability ----
logging:
  level: INFO
  format: json

metrics:
  enabled: true
  serviceMonitor:
    enabled: false
    interval: 30s
    labels: {}

# ---- Probes ----
probes:
  liveness:
    initialDelaySeconds: 10
    periodSeconds: 30
    timeoutSeconds: 5
  readiness:
    initialDelaySeconds: 5
    periodSeconds: 10
    timeoutSeconds: 3
```

---

## 10. Struttura Repository

```
ephemeral-sites/
├── README.md
├── LICENSE                                # Apache-2.0
├── CHANGELOG.md
├── CONTRIBUTING.md
├── pyproject.toml
├── poetry.lock
├── Dockerfile                             # multi-stage, singola immagine
├── docker-compose.yml                     # dev locale
├── .dockerignore
├── .gitignore
├── .github/
│   └── workflows/
│       ├── test.yml                       # pytest + lint (ruff/black)
│       ├── build-push.yml                 # Docker → ghcr.io
│       └── release.yml                    # tag → GitHub Release + chart bump
│
├── src/
│   └── ephemeral_sites/
│       ├── __init__.py
│       ├── __main__.py                    # entry: python -m ephemeral_sites [api|cleanup]
│       ├── config.py                      # Pydantic Settings from env
│       ├── db.py                          # SQLite + migrations
│       ├── models.py                      # Pydantic schemas
│       ├── auth.py                        # API key + delete token + bcrypt
│       ├── validator.py                   # ZIP safety (traversal, bomb, symlink)
│       ├── storage.py                     # Filesystem ops + atomic swap
│       ├── slug.py                        # Generator adjective-animal-hex
│       ├── quota.py                       # Global storage quota check
│       ├── api/
│       │   ├── __init__.py
│       │   ├── app.py                     # FastAPI factory
│       │   ├── routes_sites.py            # CRUD endpoints
│       │   ├── routes_health.py           # /healthz /readyz /metrics
│       │   └── middleware.py              # rate limit, logging, request ID
│       ├── server/
│       │   ├── __init__.py
│       │   ├── app.py                     # FastAPI factory server statico
│       │   ├── serve.py                   # SPA fallback + security headers
│       │   └── ephemeral_routes.py        # /_ephemeral/info, /config.json
│       └── cleanup/
│           ├── __init__.py
│           └── run.py                     # Entry CronJob
│
├── tests/
│   ├── conftest.py
│   ├── unit/
│   │   ├── test_validator.py
│   │   ├── test_slug.py
│   │   ├── test_auth.py
│   │   ├── test_storage_atomic.py
│   │   ├── test_quota.py
│   │   └── test_config.py
│   ├── integration/
│   │   ├── test_api_upsert.py
│   │   ├── test_api_lifecycle.py
│   │   ├── test_api_patch.py
│   │   ├── test_api_list.py
│   │   ├── test_runtime_config.py
│   │   ├── test_noindex_header.py
│   │   ├── test_permanent_ttl.py
│   │   ├── test_server_serve.py
│   │   ├── test_server_password.py
│   │   └── test_cleanup.py
│   └── fixtures/
│       ├── valid_spa.zip
│       ├── valid_spa_with_subfolder.zip
│       ├── zip_bomb.zip
│       ├── path_traversal.zip
│       ├── symlink.zip
│       └── no_index.zip
│
├── charts/
│   └── ephemeral-sites/
│       ├── Chart.yaml
│       ├── values.yaml
│       ├── values-production.example.yaml
│       ├── README.md
│       └── templates/
│           ├── _helpers.tpl
│           ├── NOTES.txt
│           ├── app-deployment.yaml
│           ├── app-service.yaml
│           ├── ingress-api.yaml
│           ├── ingress-wildcard.yaml
│           ├── certificate.yaml
│           ├── cleanup-cronjob.yaml
│           ├── pvc.yaml
│           ├── secret-auth.yaml
│           ├── serviceaccount.yaml
│           ├── networkpolicy.yaml         # opt-in
│           └── servicemonitor.yaml        # opt-in
│
├── cli/
│   ├── deploy.sh                          # PUT /api/v1/sites/{slug}
│   ├── delete.sh
│   ├── list.sh
│   └── extend.sh
│
└── docs/
    ├── installation.md
    ├── configuration.md
    ├── security.md
    ├── troubleshooting.md
    └── api-reference.md                   # auto-gen da OpenAPI FastAPI
```

### 10.1 Dockerfile (riferimento)

```dockerfile
FROM python:3.12-slim AS builder
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY pyproject.toml poetry.lock ./
RUN pip install --no-cache-dir poetry==1.8.3 && \
    poetry config virtualenvs.in-project true && \
    poetry install --only main --no-root

FROM python:3.12-slim AS runtime
RUN groupadd -g 10001 app && \
    useradd -u 10001 -g app -s /sbin/nologin -m app
WORKDIR /app
COPY --from=builder /build/.venv /app/.venv
COPY src/ /app/src/
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
USER app
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/healthz')"
ENTRYPOINT ["python", "-m", "ephemeral_sites"]
CMD ["api"]
```

---

## 11. Testing Strategy

### 11.1 TDD obbligatorio

Red → Green → Refactor per ogni feature. Un test rosso è una specifica scritta in codice.

### 11.2 Piramide

| Livello | % attesa | Dove | Esempi |
|---------|----------|------|--------|
| Unit | ~70% | `tests/unit/` | validator, slug, storage, auth, quota |
| Integration | ~25% | `tests/integration/` | API flow con SQLite temp + storage temp |
| E2E | ~5% | Manuale/CI helm test | Helm install + curl reale |

### 11.3 Test critici da implementare da subito

**Security (red first, devono fallire prima di implementare validator):**
```
tests/unit/test_validator.py::test_rejects_zip_bomb
tests/unit/test_validator.py::test_rejects_path_traversal_dotdot
tests/unit/test_validator.py::test_rejects_absolute_path
tests/unit/test_validator.py::test_rejects_symlink
tests/unit/test_validator.py::test_rejects_excessive_file_count
tests/unit/test_validator.py::test_rejects_excessive_decompressed_size
tests/unit/test_validator.py::test_rejects_non_whitelisted_extension
tests/unit/test_validator.py::test_flattens_single_toplevel_folder
tests/unit/test_validator.py::test_requires_index_html
```

**Business logic:**
```
tests/unit/test_slug.py::test_slug_format_matches_regex
tests/unit/test_slug.py::test_slug_collision_retries_up_to_5
tests/unit/test_storage_atomic.py::test_overwrite_no_404_window
tests/unit/test_storage_atomic.py::test_rollback_on_extraction_error
tests/unit/test_quota.py::test_exceeds_global_quota_returns_507
tests/unit/test_auth.py::test_bcrypt_hash_verification
tests/unit/test_auth.py::test_invalid_key_returns_401
```

**API integration:**
```
tests/integration/test_api_upsert.py::test_put_creates_site
tests/integration/test_api_upsert.py::test_put_same_slug_replaces_content
tests/integration/test_api_upsert.py::test_put_same_slug_no_404_during_swap
tests/integration/test_api_lifecycle.py::test_post_get_patch_delete
tests/integration/test_runtime_config.py::test_config_json_served_from_param
tests/integration/test_noindex_header.py::test_default_noindex_applied
tests/integration/test_noindex_header.py::test_allow_indexing_omits_header
tests/integration/test_permanent_ttl.py::test_ttl_minus_one_stored_as_null
tests/integration/test_permanent_ttl.py::test_cleanup_ignores_permanent_sites
tests/integration/test_server_password.py::test_password_protected_requires_auth
tests/integration/test_server_serve.py::test_spa_fallback_to_index_html
tests/integration/test_server_serve.py::test_static_asset_not_fallback
tests/integration/test_cleanup.py::test_expired_site_removed_from_fs_and_db
```

### 11.4 Coverage target

- **Logic business** (validator, auth, storage, slug, quota): ≥ 90%
- **API routes**: ≥ 80%
- **Complessivo**: ≥ 80%

Enforcement: CI fallisce se coverage scende sotto soglia.

### 11.5 Linting & formatting

- `ruff check` + `ruff format` (o `black`)
- `mypy` opzionale (il repo main non usa type hints — seguire convenzione)
- Pre-commit hook raccomandato

---

## 12. Deployment & Operations

### 12.1 Prerequisiti infrastruttura

- [ ] k3s su GCP (o qualsiasi k8s ≥ 1.26)
- [ ] IP statico GCP configurato
- [ ] Dominio registrato e delegato a Cloudflare (vedi §8.1)
- [ ] cert-manager installato (vedi §8.2)
- [ ] `ClusterIssuer` staging + production applicati e `Ready=True`
- [ ] Traefik funzionante (default k3s)
- [ ] Helm 3.x installato

### 12.2 Procedura install

```bash
# 1. Namespace
kubectl create namespace ephemeral-sites

# 2. Secret auth (API key)
kubectl -n ephemeral-sites create secret generic ephemeral-sites-auth \
  --from-literal=API_KEYS="main:$(openssl rand -hex 32)"

# Salvare la key in password manager!

# 3. Customizzare values-production.yaml (da values-production.example.yaml)
cp charts/ephemeral-sites/values-production.example.yaml values-production.yaml
# Modificare: domain, wildcardHost, apiHost, dns.zone, storage.size,
# app.image.repository, app.image.tag

# 4. Install
helm install ephemeral-sites ./charts/ephemeral-sites \
  -n ephemeral-sites \
  -f values-production.yaml

# 5. Verifica
kubectl -n ephemeral-sites get all,certificate,ingress
```

Tempo atteso per setup iniziale completo (primo cert wildcard): 2-5 minuti.

### 12.3 Upgrade

```bash
helm upgrade ephemeral-sites ./charts/ephemeral-sites \
  -n ephemeral-sites \
  -f values-production.yaml
```

SQLite migrations: eseguite automaticamente all'avvio app (idempotenti, con backup pre-migration).

### 12.4 Smoke test post-install

```bash
# 1. API health
curl -v https://api.preview.miei-esperimenti.dev/healthz
# Atteso: 200 OK

# 2. Deploy di un sito di test
cat > index.html <<'EOF'
<!DOCTYPE html><html><body><h1>Hello ephemeral-sites!</h1></body></html>
EOF
zip test.zip index.html

curl -X PUT "https://api.preview.miei-esperimenti.dev/api/v1/sites/hello-test" \
  -H "Authorization: Bearer $API_KEY" \
  -F "file=@test.zip" \
  -F "ttl_seconds=3600"

# 3. Verifica sito servito
curl -v https://hello-test.preview.miei-esperimenti.dev/
# Atteso: 200 OK con HTML

# 4. Verifica noindex header
curl -I https://hello-test.preview.miei-esperimenti.dev/ | grep -i robots
# Atteso: X-Robots-Tag: noindex, nofollow, noarchive

# 5. Delete
curl -X DELETE "https://api.preview.miei-esperimenti.dev/api/v1/sites/hello-test" \
  -H "Authorization: Bearer $API_KEY"
# Atteso: 204 No Content
```

### 12.5 Backup

**Strategia consigliata**: rsync settimanale del PVC verso storage off-site.

```bash
# Esempio cron via ssh + tar
kubectl exec -n ephemeral-sites deploy/ephemeral-sites -- \
  tar czf - /data | ssh backup-server \
  "cat > backups/ephemeral-sites-$(date +%F).tar.gz"
```

Alternative (in ordine di robustezza):
1. Longhorn snapshot → S3 remoto (Backblaze B2 / R2)
2. Velero con provider cloud
3. Manuale rsync settimanale

### 12.6 Monitoring minimale

Dashboard Grafana suggerito (4 pannelli):
1. Siti attivi (`ephemeral_sites_total`)
2. Storage usato % (`ephemeral_sites_storage_bytes / maxTotalStorageBytes * 100`)
3. Deploy/day (rate `ephemeral_sites_created_total`)
4. Hits totali per sito (top 10)

**Alert:**
- Storage > 90% capacity → email/Telegram
- Deploy rate > 100/min (API key potenzialmente leakata)
- Certificate `notAfter - now < 14 giorni` (rinnovo non avvenuto)

### 12.7 Runbook issue comuni

**Siti non si creano**:
1. `kubectl logs -n ephemeral-sites -l app=ephemeral-sites --tail=100`
2. `kubectl exec -it deploy/ephemeral-sites -- df -h /data`
3. `kubectl exec -it deploy/ephemeral-sites -- sqlite3 /data/db/ephemeral-sites.db ".tables"`

**Subdomain non risolve**:
1. `dig A test.preview.<domain> +short` → IP GCP?
2. `kubectl get ingress -n ephemeral-sites`
3. `kubectl get certificate -n ephemeral-sites`

**Certificate non emesso**:
1. `kubectl describe certificate -n ephemeral-sites`
2. `kubectl get challenges -n ephemeral-sites`
3. `kubectl logs -n cert-manager deploy/cert-manager`

**Cleanup non funziona**:
1. `kubectl get cronjob -n ephemeral-sites`
2. `kubectl get jobs -n ephemeral-sites --sort-by=.metadata.creationTimestamp`
3. `kubectl logs job/ephemeral-sites-cleanup-xxxxx`

---

## 13. Open Points

Aree che il team deve risolvere prima/durante l'implementazione.

### 🟡 OP-1: Nome dominio definitivo
Placeholder usato: `miei-esperimenti.dev`. **Da sostituire** in tutti i file `values.yaml`, docs, esempi. L'owner comunicherà il dominio reale dopo registrazione.

### 🟡 OP-2: IP statico GCP
Placeholder `<IP_STATICO_GCP>`. Da sostituire in documentazione DNS. Non appare nei file del chart (è solo DNS-level).

### 🟡 OP-3: Owner email per Let's Encrypt
Placeholder `OWNER_EMAIL@example.com` nei ClusterIssuer. Da sostituire con email reale dell'owner (LE invia notifiche di scadenza a questo indirizzo).

### 🟡 OP-4: Nome organizzazione GitHub
Placeholder `ghcr.io/OWNER/ephemeral-sites`. Da sostituire con org/user GitHub reale dove verrà pubblicata l'immagine.

### 🟢 OP-5: Event log retention
Default 90 giorni. **Confermato** dal team al primo sprint; modificabile da `values.yaml`.

### 🟢 OP-6: Dimensione PVC iniziale
Default 50 GiB, quota 40 GiB. **Confermato** per profilo d'uso atteso. Ridimensionabile in futuro (StorageClass dipendente).

### 🟢 OP-7: Dashboard HTML
Rimandata a v1.1. v1.0 è API-only.

---

## 14. Non-Goals

Feature **mai** implementate, nemmeno in futuro, per mantenere focus e semplicità:

- ❌ Server-side rendering / runtime Node.js
- ❌ Build step integrato (Webpack, Vite, ecc.)
- ❌ Database per siti ospitati
- ❌ Proxy verso backend dinamico
- ❌ CDN / cache distribuita (usare Cloudflare proxy davanti se serve)
- ❌ Custom domain per singolo sito (solo wildcard subdomain)
- ❌ Multi-tenancy / workspace / team
- ❌ Git integration (push → deploy)
- ❌ OpenTelemetry distributed tracing
- ❌ Webhook inbound

Per questi use case esistono Netlify, Vercel, Kubero. `ephemeral-sites` fa **una cosa sola: serve SPA statiche effimere via API**.

---

## 15. Definition of Done

Il progetto v1.0 è completo quando:

### Code quality
- [ ] Tutti i test unit + integration passano su CI
- [ ] Coverage ≥ 80% complessivo, ≥ 90% su logica business
- [ ] `ruff check` + `ruff format` puliti
- [ ] Helm chart passa `helm lint` e `helm template` senza warning
- [ ] Vulnerability scan immagine Docker: zero CVE HIGH/CRITICAL

### Funzionalità
- [ ] Install su k3s fresco seguendo solo README funziona end-to-end
- [ ] Deploy di una SPA React reale produce URL pubblico con HTTPS valido
- [ ] Re-deploy stesso slug 10 volte di fila senza 404 intermedi
- [ ] Sito permanente (`ttl=-1`) sopravvive a più cicli di cleanup
- [ ] Default `X-Robots-Tag: noindex` verificato in produzione
- [ ] Password protection verificata (401 senza, 200 con)
- [ ] `runtime_config` iniettato e leggibile da SPA
- [ ] Cleanup elimina siti scaduti entro 5 min dal TTL
- [ ] Quota globale blocca upload quando superata (test con file da 41 GiB)
- [ ] Backup PVC testato (restore funziona)
- [ ] Rotation API key testata senza downtime

### Documentazione
- [ ] README con quickstart < 10 comandi
- [ ] `docs/installation.md` completo
- [ ] `docs/security.md` include threat model e mitigazioni
- [ ] `docs/troubleshooting.md` copre i 4 scenari del runbook
- [ ] OpenAPI schema auto-generato e accessibile da `/docs`
- [ ] Helm chart README auto-generato con `helm-docs`

### Operational
- [ ] Immagine Docker pubblicata su `ghcr.io` con tag `1.0.0`
- [ ] Helm chart pubblicato (OCI o Pages)
- [ ] GitHub Release creata con changelog

---

## 16. Roadmap & Ordine di Implementazione

### 16.1 Ordine canonico (TDD)

Implementare **strettamente** in quest'ordine. Ogni step: red test → green code → refactor. Ogni step deve passare il CI prima di procedere.

| # | Step | Deliverable | Test chiave | Effort |
|---|------|-------------|-------------|--------|
| 1 | Scaffolding | pyproject, Dockerfile, CI base | `test_smoke.py::test_imports_ok` | 0.5d |
| 2 | Validator ZIP | `validator.py` | Tutti i `test_validator.py` | 1d |
| 3 | Slug generator | `slug.py` | `test_slug.py` | 0.5d |
| 4 | DB + migrations | `db.py` | `test_db.py::test_migration_v0_to_v1` | 0.5d |
| 5 | Storage atomic | `storage.py` | `test_storage_atomic.py` | 1d |
| 6 | Auth | `auth.py` | `test_auth.py` | 0.5d |
| 7 | Quota check | `quota.py` | `test_quota.py` | 0.5d |
| 8 | API PUT upsert | `api/routes_sites.py` PUT | `test_api_upsert.py` | 1d |
| 9 | API CRUD rimanenti | GET, DELETE, PATCH, POST, LIST | `test_api_lifecycle.py` | 1d |
| 10 | Runtime config injection | `server/serve.py` + logic | `test_runtime_config.py` | 0.5d |
| 11 | Static server + SPA fallback + security headers | `server/serve.py` | `test_server_serve.py`, `test_noindex_header.py` | 1d |
| 12 | Password protection | `server/serve.py` basic auth | `test_server_password.py` | 0.5d |
| 13 | Cleanup CronJob | `cleanup/run.py` | `test_cleanup.py`, `test_permanent_ttl.py` | 0.5d |
| 14 | Metrics + health | `api/routes_health.py` | `test_metrics.py` | 0.5d |
| 15 | Helm chart | `charts/ephemeral-sites/` | `helm lint` + `helm template` | 1d |
| 16 | CLI bash | `cli/*.sh` | Manuale + README | 0.5d |
| 17 | README + docs | Tutto `docs/` | Review | 0.5d |
| 18 | E2E su k3d | CI workflow | `helm test` | 0.5d |

**Totale**: ~10-12 giorni/persona.

### 16.2 Roadmap future

**v1.1** (nice-to-have post-launch)
- Dashboard HTML minimale (lista siti, countdown TTL, delete button)
- Backup automatico DB pre-migration
- Cleanup log a livello INFO solo su azione

**v2.0** (solo se un giorno il servizio viene esteso ad altri utenti)
- Multi-tenancy con namespace per org
- Postgres + MinIO backend
- Webhooks su lifecycle events
- CLI binario Go
- OpenTelemetry tracing
- Split deployment mode per RWX / HA

---

## Appendice A — Contatti & Ownership

| Ruolo | Nome |
|-------|------|
| Product Owner | Owner del progetto (TBD) |
| Tech Lead | Team di sviluppo |
| Ops | Owner (self-ops) |
| Approver spec | Owner |

## Appendice B — Riferimenti esterni

- [Claude Agent SDK documentation](https://docs.claude.com) — stile coding repository
- [Traefik Documentation](https://doc.traefik.io/traefik/)
- [cert-manager Documentation](https://cert-manager.io/docs/)
- [Let's Encrypt Rate Limits](https://letsencrypt.org/docs/rate-limits/)
- [Cloudflare API Tokens](https://developers.cloudflare.com/api/tokens/)
- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [SQLite WAL mode](https://www.sqlite.org/wal.html)

---

**FINE SPECIFICA v1.0**
**Approvata per implementazione: 2026-05-06**
