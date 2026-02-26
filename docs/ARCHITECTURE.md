# SKRef Architecture — Sovereign Encrypted Reference Vaults

## Overview

SKRef is Tier 3 of the skcapstone sovereign agent storage model. It provides GPG-encrypted, backend-agnostic file vaults accessible through FUSE mounts (Linux/macOS), Windows virtual drives, and WebDAV proxies (phones, remote devices). Files are ciphertext at rest on any backend — your CapAuth PGP key is the only thing that unlocks them.

```mermaid
graph TB
    subgraph "skcapstone Storage Tiers"
        T1["Tier 1: Auth Core<br/>~/.skcapstone/sync/<br/>≤5 MB — always synced"]
        T2["Tier 2: GTD Actions<br/>~/.skcapstone/gtd/<br/>≤1 MB — optional sync"]
        T3["Tier 3: SKRef Vaults<br/>Unbounded — selective sync<br/>Any backend, encrypted at rest"]
    end
    T1 -->|"Identity, trust, FEB seeds"| phone["Phone"]
    T2 -->|"Task lists (JSON)"| phone
    T3 -->|"WebDAV proxy"| phone
    T3 -->|"FUSE mount"| desktop["Desktop"]
    T3 -->|"Windows virtual FS"| windows["Windows"]
```

---

## System Architecture

```mermaid
graph TD
    subgraph "User-Facing Layer"
        FM["File Manager<br/>(Nemo, Nautilus, Dolphin, Explorer)"]
        CLI["skref CLI<br/>put / ls / open / mount / share / serve"]
        WEBDAV_CLIENT["WebDAV Client<br/>(Phone apps, remote)"]
    end

    subgraph "Network Layer"
        TS_FUNNEL["Tailscale Funnel<br/>Valid TLS, global FQDN<br/>your-machine.tail1234.ts.net"]
    end

    subgraph "Access Layer"
        FUSE["FUSE Mount<br/>pyfuse3 (Linux/macOS)"]
        WINFSP["WinFsp / Dokan<br/>Windows virtual FS"]
        WEBDAV_SRV["WebDAV Proxy Server<br/>localhost:8443<br/>Decrypt-on-read, encrypt-on-write"]
    end

    subgraph "Core Layer"
        VAULT["Vault<br/>Encrypt/Decrypt dispatch"]
        SHARE["Share Manager<br/>Peer keys, public links, ACLs"]
    end

    subgraph "Crypto Layer"
        GPG["GPG Engine<br/>CapAuth PGP key"]
        KEYRING["Key Manager<br/>auto-detect, peer keys, ephemeral keys"]
    end

    subgraph "Backend Layer (dumb storage)"
        LOCAL["Local FS<br/>Disk, USB, NAS"]
        NEXTCLOUD["Nextcloud<br/>WebDAV"]
        S3["S3 / MinIO<br/>Any S3-compatible"]
        GDRIVE["Google Drive<br/>API"]
    end

    FM --> FUSE
    FM --> WINFSP
    CLI --> VAULT
    WEBDAV_CLIENT --> TS_FUNNEL
    TS_FUNNEL --> WEBDAV_SRV

    FUSE --> VAULT
    WINFSP --> VAULT
    WEBDAV_SRV --> VAULT

    VAULT --> GPG
    VAULT --> SHARE
    SHARE --> KEYRING
    GPG --> KEYRING

    VAULT --> LOCAL
    VAULT --> NEXTCLOUD
    VAULT --> S3
    VAULT --> GDRIVE
```

---

## Data Flow: Read (Decrypt)

```mermaid
sequenceDiagram
    participant User as File Manager / App
    participant FUSE as FUSE / WinFsp
    participant Vault
    participant Crypto as GPG Engine
    participant Backend as Backend (local/cloud)

    User->>FUSE: open("contract.pdf")
    FUSE->>Vault: read("contract.pdf")
    Vault->>Backend: get("contract.pdf.gpg")
    Backend-->>Vault: ciphertext bytes
    Vault->>Crypto: decrypt(ciphertext, user_key)
    Crypto-->>Vault: plaintext bytes
    Vault-->>FUSE: plaintext bytes
    FUSE-->>User: file contents (readable)
```

## Data Flow: Write (Encrypt)

```mermaid
sequenceDiagram
    participant User as File Manager / App
    participant FUSE as FUSE / WinFsp
    participant Vault
    participant Crypto as GPG Engine
    participant Backend as Backend (local/cloud)

    User->>FUSE: write("notes.md", data)
    FUSE->>FUSE: buffer in memory
    User->>FUSE: close()
    FUSE->>Vault: write("notes.md", plaintext)
    Vault->>Crypto: encrypt(plaintext, [owner_key, peer_keys...])
    Crypto-->>Vault: ciphertext bytes
    Vault->>Backend: put("notes.md.gpg", ciphertext)
    Backend-->>Vault: OK
```

---

## File Manager Integration (FUSE)

Yes — FUSE integration is seamless in Nemo, Nautilus, Dolphin, Thunar, and any GTK/Qt file manager. When you run `skref mount ~/vault`, the `~/vault` directory appears as a regular folder in your sidebar. Double-click a PDF — it decrypts, opens in your viewer. Drag a file in — it encrypts and stores. The file manager doesn't know or care that there's crypto underneath.

```mermaid
graph LR
    subgraph "What the user sees"
        A["~/vault/<br/>├── legal/contract.pdf<br/>├── health/lab.pdf<br/>└── recipes/bread.md"]
    end
    subgraph "What's on disk"
        B["~/.skcapstone/vaults/personal/<br/>├── legal/contract.pdf.gpg<br/>├── health/lab.pdf.gpg<br/>└── recipes/bread.md.gpg"]
    end
    A -.->|"FUSE translates"| B
```

File managers that work out of the box:
- **Linux:** Nemo, Nautilus (GNOME Files), Dolphin, Thunar, PCManFM, Caja
- **macOS:** Finder (with macFUSE installed)
- **Windows:** Explorer (with WinFsp — see Phase 5)

---

## Sharing Model

```mermaid
graph TB
    subgraph "Sharing Tiers"
        S1["Sovereign Peer<br/>Both parties have CapAuth identity<br/>Encrypt to multiple PGP keys"]
        S2["Trusted External<br/>Has PGP key, not in trust network<br/>Add key as vault peer"]
        S3["Anonymous / Public<br/>No PGP key, no identity<br/>Time-limited download link"]
    end

    S1 -->|"GPG multi-recipient<br/>peers: [fp1, fp2]"| VAULT["Vault"]
    S2 -->|"Import public key<br/>add as recipient"| VAULT
    S3 -->|"Ephemeral AES key<br/>HTTPS download link<br/>with passphrase"| PROXY["Share Proxy"]
    PROXY -->|"Decrypt with owner key<br/>re-encrypt with ephemeral key<br/>serve over HTTPS"| ANON["Anonymous Recipient"]
```

### Sovereign Peer Sharing
Both parties have CapAuth identities. The file is encrypted to multiple PGP recipients. Both can decrypt natively. Full trust chain visible.

### Trusted External Sharing
Recipient has a PGP key but isn't in your trust network. You import their public key and add them as a vault peer. They can decrypt but aren't part of your sovereign sync.

### Anonymous / Public Sharing
Recipient has no PGP key at all. The system:
1. Decrypts the file with your key (server-side or local)
2. Generates a random AES-256 passphrase
3. Re-encrypts with the passphrase
4. Creates a time-limited HTTPS download link
5. You send the link + passphrase to the recipient via any channel

The passphrase is never stored on the server. The link expires. The original vault ciphertext is never exposed.

---

## Backend Interface Contract

Every backend implements the same dumb interface. It stores and retrieves bytes — it never sees plaintext.

```mermaid
classDiagram
    class Backend {
        <<abstract>>
        +put(rel_path: str, data: bytes) void
        +get(rel_path: str) bytes
        +delete(rel_path: str) void
        +list_dir(rel_path: str) list~FileEntry~
        +exists(rel_path: str) bool
        +mkdir(rel_path: str) void
        +file_size(rel_path: str) int
    }

    class LocalBackend {
        -root: Path
        +put()
        +get()
        +delete()
        +list_dir()
        +exists()
        +mkdir()
        +file_size()
    }

    class NextcloudBackend {
        -webdav_url: str
        -auth: tuple
        +put()
        +get()
        +delete()
        +list_dir()
        +exists()
        +mkdir()
        +file_size()
    }

    class S3Backend {
        -bucket: str
        -prefix: str
        -client: boto3.Client
        +put()
        +get()
        +delete()
        +list_dir()
        +exists()
        +mkdir()
        +file_size()
    }

    class GDriveBackend {
        -folder_id: str
        -credentials: Credentials
        +put()
        +get()
        +delete()
        +list_dir()
        +exists()
        +mkdir()
        +file_size()
    }

    Backend <|-- LocalBackend
    Backend <|-- NextcloudBackend
    Backend <|-- S3Backend
    Backend <|-- GDriveBackend
```

---

## Configuration

All vaults configured in `~/.skcapstone/vaults.yaml`:

```yaml
default_vault: personal
identity_home: ~/.skcapstone/identity

vaults:
  personal:
    backend: local
    path: ~/.skcapstone/vaults/personal
    encrypted: true
    key: auto                    # auto-detect CapAuth PGP key
    peers: []

  work:
    backend: nextcloud
    url: https://cloud.example.com/remote.php/dav/files/user/skref/
    encrypted: true
    key: auto
    peers:
      - "AABB1122..."           # colleague's PGP fingerprint

  archive:
    backend: s3
    bucket: my-skref-archive
    region: us-east-1
    encrypted: true
    key: auto

  shared-public:
    backend: local
    path: /mnt/nas/shared
    encrypted: false             # no crypto — team-readable
```

---

## Platform Support Matrix

| Feature | Linux | macOS | Windows |
|---------|-------|-------|---------|
| CLI (init, ls, put, open) | Yes | Yes | Yes |
| GPG encrypt/decrypt | Yes | Yes | Yes (Gpg4win) |
| FUSE mount | pyfuse3 + fuse3 | pyfuse3 + macFUSE | WinFsp (Phase 5) |
| File manager integration | Nemo, Nautilus, Dolphin, etc. | Finder | Explorer |
| WebDAV proxy | Yes | Yes | Yes |
| Tailscale Funnel | Yes | Yes | Yes |
| Auto-mount on login | systemd unit | launchd plist | Task Scheduler |

---

## Phase Roadmap

```mermaid
gantt
    title SKRef Development Phases
    dateFormat YYYY-MM-DD
    axisFormat %b %Y

    section Phase 1 — Local Backend
    Local backend + crypto + FUSE + CLI  :done, p1, 2026-02-25, 1d

    section Phase 2 — Nextcloud/WebDAV
    WebDAV backend                       :p2a, after p1, 5d
    Auth integration (app password)      :p2b, after p2a, 2d

    section Phase 3 — S3/MinIO
    S3 backend (boto3)                   :p3a, after p1, 4d
    MinIO self-hosted support            :p3b, after p3a, 2d

    section Phase 4 — WebDAV Proxy
    Proxy server (aiohttp)               :p4a, after p2a, 5d
    Phone client docs                    :p4b, after p4a, 2d

    section Phase 5 — Windows
    WinFsp FUSE adapter                  :p5a, after p1, 7d
    Dokan fallback                       :p5b, after p5a, 3d
    Explorer integration                 :p5c, after p5a, 3d

    section Phase 6 — Sharing
    Multi-recipient (sovereign)          :p6a, after p1, 3d
    Public link sharing                  :p6b, after p4a, 5d
    Share CLI + web UI                   :p6c, after p6b, 4d
```

---

## Tailscale Funnel — Global Access Without Port Forwarding

Tailscale Funnel is the recommended way to expose the WebDAV proxy. It replaces self-signed certificates, port forwarding, and dynamic DNS with a single command.

```mermaid
sequenceDiagram
    participant Phone as Phone (anywhere)
    participant TS as Tailscale Funnel<br/>TLS termination
    participant Proxy as SKRef WebDAV Proxy<br/>localhost:8443
    participant Vault
    participant Backend

    Phone->>TS: HTTPS GET https://my-desktop.tail1234.ts.net/legal/contract.pdf
    TS->>TS: Verify TLS (Let's Encrypt cert)
    TS->>Proxy: HTTP GET /legal/contract.pdf (tunnel to localhost)
    Proxy->>Vault: read("legal/contract.pdf")
    Vault->>Backend: get("legal/contract.pdf.gpg")
    Backend-->>Vault: ciphertext
    Vault-->>Proxy: plaintext
    Proxy-->>TS: 200 OK + plaintext
    TS-->>Phone: HTTPS 200 OK + plaintext (encrypted in transit)
```

**How it works:**

1. `skref serve --tailscale` binds the WebDAV proxy to `127.0.0.1:8443` (localhost only)
2. `tailscale serve https / http://127.0.0.1:8443` tells Tailscale to proxy HTTPS → local HTTP
3. `tailscale funnel 8443` makes the FQDN publicly reachable from the internet
4. Tailscale provisions a valid Let's Encrypt certificate for your FQDN automatically
5. Phone connects to `https://your-machine.tail1234.ts.net/` — works from cellular, any WiFi, anywhere

**What Tailscale Funnel gives you:**

| Feature | Without Tailscale | With Tailscale Funnel |
|---------|---|---|
| TLS certificate | Self-signed (phone warns) | Valid Let's Encrypt (automatic) |
| Port forwarding | Required on router | None needed |
| DNS | Dynamic DNS or static IP | Stable FQDN on `ts.net` |
| Network access | Same LAN only | Global (internet-routable) |
| Firewall config | Manual rules | Tailscale ACLs |
| Setup complexity | ~10 steps | `--tailscale` flag |

**Integration with skcapstone setup:**

Tailscale detection is built into `src/skref/tailscale.py` and will be wired into `skcapstone install` so that:
- `skcapstone doctor` checks for Tailscale and reports FQDN
- `skcapstone install` optionally sets up Tailscale Serve + Funnel for skref
- The FQDN is stored in `~/.skcapstone/vaults.yaml` under `serve.tailscale_fqdn`

---

## Security Model

```mermaid
graph TD
    subgraph "At Rest"
        CIPHER["All files stored as .gpg<br/>GPG binary format (RFC 4880)"]
    end
    subgraph "In Transit"
        TLS["HTTPS/TLS to cloud backends<br/>WebDAV proxy via Tailscale Funnel<br/>Valid Let's Encrypt certificate"]
    end
    subgraph "In Use (mounted)"
        MEM["Plaintext only in memory<br/>FUSE: buffered per open fd<br/>Never written to disk"]
    end
    subgraph "Key Management"
        KEY["CapAuth PGP key<br/>Auto-detected from identity.json<br/>or GPG keyring"]
    end

    KEY --> CIPHER
    KEY --> MEM
    CIPHER --> TLS
```

**Threat model:**
- Backend compromise (cloud provider, disk theft): attacker sees only `.gpg` ciphertext. Useless without the private key.
- FUSE mount compromise: plaintext exists only in memory for the duration of a file open/close cycle. No plaintext cache on disk.
- Key compromise: standard PGP key revocation. Re-encrypt vault with new key via `skref rekey`.
- Shared vault peer removal: re-encrypt files without the removed peer's key via `skref rekey --remove-peer`.

---

## Directory Structure

```
skref/
├── pyproject.toml
├── README.md
├── docs/
│   ├── ARCHITECTURE.md          ← you are here
│   ├── USAGE.md                 ← end-user guide
│   ├── CONTRIBUTING.md          ← developer guide
│   └── phases/
│       ├── PHASE-1.md           ← local backend (done)
│       ├── PHASE-2.md           ← Nextcloud/WebDAV backend
│       ├── PHASE-3.md           ← S3/MinIO backend
│       ├── PHASE-4.md           ← WebDAV proxy server
│       ├── PHASE-5.md           ← Windows support
│       └── PHASE-6.md           ← sharing (peers + anonymous)
├── src/skref/
│   ├── __init__.py
│   ├── models.py                ← Pydantic: VaultConfig, SkrefConfig
│   ├── config.py                ← YAML load/save
│   ├── crypto.py                ← GPG encrypt/decrypt, key detection
│   ├── vault.py                 ← Vault class (backend + crypto)
│   ├── fuse_mount.py            ← pyfuse3 FUSE filesystem
│   ├── cli.py                   ← Click CLI
│   ├── sharing.py               ← (Phase 6) share manager
│   ├── webdav_proxy.py          ← (Phase 4) WebDAV server
│   └── backends/
│       ├── __init__.py
│       ├── base.py              ← Abstract Backend interface
│       ├── local.py             ← Local filesystem backend
│       ├── nextcloud.py         ← (Phase 2) WebDAV backend
│       ├── s3.py                ← (Phase 3) S3/MinIO backend
│       └── gdrive.py            ← (Future) Google Drive backend
└── tests/
    ├── test_backend_local.py
    ├── test_config.py
    ├── test_crypto.py
    ├── test_vault.py
    ├── test_backend_nextcloud.py  ← (Phase 2)
    ├── test_backend_s3.py         ← (Phase 3)
    ├── test_webdav_proxy.py       ← (Phase 4)
    └── test_sharing.py            ← (Phase 6)
```
