# SKRef — Sovereign Encrypted Reference Vaults

**FUSE-mounted, GPG-encrypted file vaults that sit on any backend. Your CapAuth PGP key is the only thing that unlocks them.**

Mount a folder. Browse files normally. On disk (and on the cloud backend) it's all ciphertext. Decrypt-on-read, encrypt-on-write. Transparent. Sovereign.

```
You see:                        Backend stores:
~/vault/                        ~/.skcapstone/vaults/personal/
├── legal/                      ├── legal/
│   └── contract.pdf            │   └── contract.pdf.gpg
├── health/                     ├── health/
│   └── bloodwork.pdf           │   └── bloodwork.pdf.gpg
└── recipes/                    └── recipes/
    └── banana-bread.md             └── banana-bread.md.gpg
```

Part of the **skcapstone** three-tier storage model:

| Tier | Purpose | Size | Phone? |
|------|---------|------|--------|
| **1** `~/.skcapstone/sync/` | Auth seeds (identity, trust) | ~2-5 MB | Always |
| **2** `~/.skcapstone/gtd/` | GTD task lists | ~100 KB | Optional |
| **3** SKRef vaults | Reference material, docs, files | Unbounded | Via WebDAV proxy |

---

## Quick start

```bash
# Install
pip install -e skref/

# Initialize a vault
skref init --name personal --encrypted

# Store a file (GPG-encrypts to your CapAuth key)
skref put ~/Documents/contract.pdf --vault personal

# List vault contents (shows plaintext names)
skref ls --vault personal

# Open a file (decrypts to tmpfs, opens with default viewer, cleans up)
skref open contract.pdf --vault personal

# FUSE mount — the good stuff (requires pip install skref[fuse])
skref mount ~/vault --vault personal
# Now: ls ~/vault/  → see your files
#       xdg-open ~/vault/contract.pdf  → decrypts on the fly
#       cp newfile.pdf ~/vault/  → encrypts and stores
#       Ctrl-C or umount ~/vault  → done, no plaintext on disk
```

## FUSE mount requirements

```bash
# Python dependency
pip install skref[fuse]

# Linux
sudo apt install fuse3 libfuse3-dev     # Debian/Ubuntu
sudo pacman -S fuse3                     # Arch/Manjaro

# macOS
# Install macFUSE: https://osxfuse.github.io/
```

## Vault config

Stored at `~/.skcapstone/vaults.yaml`:

```yaml
default_vault: personal
vaults:
  personal:
    backend: local
    path: "~/.skcapstone/vaults/personal"
    encrypted: true
    key: auto           # uses CapAuth PGP key
    peers: []           # add peer fingerprints for shared vaults

  shared:
    backend: local
    path: "/mnt/nas/shared-vault"
    encrypted: false    # team-readable without keys
```

## Encrypted vs. unencrypted

Each vault independently chooses:

- **Encrypted (default):** Files stored as `.gpg` on the backend. Only your PGP key (and authorized peer keys) can read them. Safe to put on any cloud — Nextcloud, S3, Google Drive — they see ciphertext only.
- **Unencrypted:** Plaintext storage. For shared/public/non-sensitive content. No crypto overhead.

## Backends (Phase 1: local, more coming)

| Backend | Status | Use case |
|---------|--------|----------|
| **local** | Done | Local disk, USB, NAS mount |
| **nextcloud** | Planned | WebDAV to Nextcloud/ownCloud |
| **s3** | Planned | AWS S3 / MinIO / any S3-compatible |
| **gdrive** | Planned | Google Drive API |

The backend is dumb storage — just put/get bytes. The crypto layer is independent. Once you encrypt, the backend doesn't matter.

## How it works

```
   skref mount ~/vault --vault personal
         │
         ▼
   ┌─────────────┐
   │  FUSE layer  │  ← You see plaintext files here
   └──────┬──────┘
          │
   ┌──────▼──────┐
   │   Vault      │  ← Encrypts on write, decrypts on read
   └──────┬──────┘
          │
   ┌──────▼──────┐
   │   Backend    │  ← Stores .gpg ciphertext (local / cloud)
   └─────────────┘
```

## License

GPL-3.0-or-later — Free as in freedom.
