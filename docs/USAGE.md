# SKRef Usage Guide

Complete guide for using SKRef sovereign encrypted vaults on every platform.

---

## Installation

### Linux / macOS

```bash
# Basic install (CLI + crypto, no FUSE)
pip install -e skref/

# With FUSE mount support
pip install -e "skref/[fuse]"

# System FUSE package (pick your distro)
sudo apt install fuse3 libfuse3-dev       # Debian / Ubuntu
sudo pacman -S fuse3                       # Arch / Manjaro
sudo dnf install fuse3 fuse3-devel         # Fedora
# macOS: install macFUSE from https://osxfuse.github.io/
```

### Windows

```powershell
# Basic install
pip install -e skref/

# GPG — install Gpg4win
# Download: https://gpg4win.org/download.html
# Ensure gpg.exe is on PATH after install

# FUSE equivalent — WinFsp (Phase 5, not yet available)
# Download: https://winfsp.dev/rel/
```

### Verify

```bash
skref --version
# skref, version 0.1.0

gpg --version
# gpg (GnuPG) 2.x.x
```

---

## Quick Start

### 1. Initialize a vault

```bash
# Encrypted vault (default — uses your CapAuth PGP key)
skref init --name personal --encrypted

# Unencrypted vault (no GPG needed, plaintext storage)
skref init --name shared --no-encrypted --path /mnt/nas/shared
```

This creates `~/.skcapstone/vaults.yaml` with your vault configuration.

### 2. Store a file

```bash
# Encrypt and store
skref put ~/Documents/contract.pdf --vault personal

# Store in a subdirectory
skref put ~/Photos/vacation.jpg --vault personal --dest photos/vacation.jpg
```

### 3. List vault contents

```bash
# List root
skref ls --vault personal

# List subdirectory
skref ls photos --vault personal
```

Output shows plaintext filenames (`.gpg` suffixes are stripped):

```
Type  Name              Size
file  contract.pdf      1.2 MB
dir   photos
file  notes.md          4.3 KB
```

### 4. Open a file

```bash
# Decrypts to tmpfs, opens with your default app, cleans up after
skref open contract.pdf --vault personal
```

On Linux this uses `xdg-open`, on macOS `open`, on Windows `os.startfile`. The decrypted file is written to tmpfs (`/run/user/$UID/skref-tmp/`) so it never hits real disk, and is deleted when the viewer closes.

### 5. FUSE mount (the best part)

```bash
# Mount your vault as a regular directory
skref mount ~/vault --vault personal

# Now use it like any folder:
ls ~/vault/
cp ~/Downloads/newfile.pdf ~/vault/        # auto-encrypts
xdg-open ~/vault/contract.pdf             # auto-decrypts
nautilus ~/vault/                          # browse in file manager

# Unmount when done
# Ctrl-C in the terminal running skref, or:
fusermount -u ~/vault
```

When mounted, `~/vault` appears in your file manager sidebar (Nemo, Nautilus, Dolphin, Thunar — all of them). Double-click files to open. Drag files in to store. It's a normal folder. The crypto is invisible.

---

## File Manager Integration

### Linux — Nemo, Nautilus, Dolphin, Thunar

FUSE mounts appear automatically in the sidebar of every GTK/Qt file manager. No configuration needed.

```bash
# Mount and open in your file manager
skref mount ~/vault --vault personal &
nemo ~/vault/       # Cinnamon/Mint
nautilus ~/vault/   # GNOME
dolphin ~/vault/    # KDE
thunar ~/vault/     # Xfce
```

### macOS — Finder

Requires macFUSE. Once installed, `skref mount` creates a volume visible in Finder.

```bash
# Install macFUSE first: https://osxfuse.github.io/
pip install "skref[fuse]"
skref mount ~/vault --vault personal
# Opens in Finder automatically
```

### Windows — Explorer (Phase 5)

Windows support uses WinFsp to create a virtual drive letter. Once Phase 5 lands:

```powershell
skref mount V: --vault personal
# V:\ appears in Explorer as a regular drive
# Double-click to open files, drag to store
```

---

## Vault Configuration

Edit `~/.skcapstone/vaults.yaml` directly or use `skref init`:

```yaml
default_vault: personal
identity_home: ~/.skcapstone/identity

vaults:
  # Encrypted local vault
  personal:
    backend: local
    path: ~/.skcapstone/vaults/personal
    encrypted: true
    key: auto                    # auto-detect from CapAuth identity
    peers: []                    # add fingerprints for shared access

  # Unencrypted NAS share
  team:
    backend: local
    path: /mnt/nas/team-files
    encrypted: false

  # Nextcloud vault (Phase 2)
  cloud:
    backend: nextcloud
    url: https://cloud.example.com/remote.php/dav/files/user/skref/
    encrypted: true
    key: auto

  # S3/MinIO vault (Phase 3)
  archive:
    backend: s3
    bucket: my-skref-archive
    region: us-east-1
    encrypted: true
    key: auto
```

### Key resolution (`key` field)

| Value | Behavior |
|-------|----------|
| `auto` (default) | Read fingerprint from `~/.skcapstone/identity/identity.json`, fall back to first GPG secret key |
| `<40-char fingerprint>` | Use this specific GPG key |
| `<key-id>` | Look up by short/long key ID |

---

## Encrypted vs. Unencrypted

Each vault independently chooses its encryption mode:

| | Encrypted (`encrypted: true`) | Unencrypted (`encrypted: false`) |
|---|---|---|
| **At rest** | `.gpg` ciphertext | Plaintext |
| **GPG key required** | Yes | No |
| **Safe on any cloud** | Yes — provider sees ciphertext only | No — provider sees your files |
| **Sharing** | Multi-recipient GPG | Filesystem permissions |
| **Performance** | Slight overhead (GPG per file) | Native speed |
| **Use case** | Private docs, health records, legal | Team wikis, public assets, non-sensitive |

---

## Platform-Specific Notes

### Linux

- FUSE works out of the box with `fuse3`
- tmpfs for `skref open` uses `/run/user/$UID/` (RAM-backed, no disk writes)
- Auto-mount on login: create a systemd user unit

```ini
# ~/.config/systemd/user/skref-personal.service
[Unit]
Description=SKRef personal vault mount

[Service]
ExecStart=/usr/local/bin/skref mount %h/vault --vault personal --foreground
ExecStop=/usr/bin/fusermount -u %h/vault

[Install]
WantedBy=default.target
```

```bash
systemctl --user enable skref-personal
systemctl --user start skref-personal
```

### macOS

- Requires macFUSE: https://osxfuse.github.io/
- GPG from Homebrew: `brew install gnupg`
- Auto-mount: launchd plist in `~/Library/LaunchAgents/`

### Windows

- GPG: install Gpg4win (https://gpg4win.org/download.html)
- FUSE: WinFsp (https://winfsp.dev/) — Phase 5
- `skref open` uses `os.startfile()` (native Windows file association)
- Config path: `%USERPROFILE%\.skcapstone\vaults.yaml`
- Until Phase 5, use `skref open` and `skref put` commands (no mount)

---

## Phone Access via Tailscale Funnel

The killer feature for phones. Your phone can't run FUSE or GPG, but it can mount a WebDAV share. `skref serve --tailscale` creates a globally-routable HTTPS endpoint with zero config.

### Setup (Automated — via skcapstone install)

If you ran `skcapstone install` and answered "Yes" to remote access, Tailscale is already set up. The wizard handled everything:

1. Installed Tailscale automatically
2. Opened your browser for a one-time login
3. Generated a reusable auth key (encrypted, saved to sync folder)
4. Enabled Funnel for global HTTPS access

Your next device will auto-join — the encrypted auth key syncs via Syncthing and is decrypted with your CapAuth PGP key. No browser login, no copy-paste, no admin console.

Just start the proxy:

```bash
skref serve --vault personal --tailscale --user me --password secret
```

### Setup (Manual — without skcapstone)

```bash
# 1. Install Tailscale (one-time)
curl -fsSL https://tailscale.com/install.sh | sh   # Linux
# Or: brew install tailscale                         # macOS
# Or: winget install Tailscale.Tailscale             # Windows

# 2. Connect to your tailnet
sudo tailscale up

# 3. Save your auth key so other devices can auto-join
#    Go to https://login.tailscale.com/admin/settings/keys
#    Generate a reusable key, then:
skref save-auth-key tskey-auth-xxxxxxxxxxxx

# 4. Start the vault proxy with Tailscale
skref serve --vault personal --tailscale --user me --password secret

# Output:
#   Tailscale detected: my-desktop.tail1234.ts.net
#   Public: https://my-desktop.tail1234.ts.net:443/
#   Valid Let's Encrypt TLS certificate
#   Accessible from anywhere
```

### Connect from Phone

**Android (Solid Explorer, Owlfiles, FE File Explorer):**
1. Add server → WebDAV
2. URL: `https://my-desktop.tail1234.ts.net:443/`
3. Username: `me`, Password: `secret`
4. Browse your vault — PDFs, images, text — all decrypted on the fly
5. Works from home WiFi, office, cellular — anywhere

**iOS (Documents by Readdle, Owlfiles, FE File Explorer):**
1. Connections → WebDAV Server
2. Same URL and credentials as above
3. Open files directly — no downloads, no GPG on phone needed

### Why Tailscale Funnel?

| Without Tailscale | With Tailscale Funnel |
|---|---|
| Self-signed cert (phone warns) | Valid Let's Encrypt cert (auto) |
| Port forwarding on router | Zero port forwarding |
| Dynamic DNS or static IP needed | Stable FQDN: `machine.ts.net` |
| LAN only | Works from cellular, anywhere |
| ~10 setup steps | One flag: `--tailscale` |

### Without Tailscale (LAN fallback)

```bash
# Generate self-signed cert
openssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem \
  -days 365 -nodes -subj '/CN=skref-local'

# Start on LAN
skref serve --vault personal --host 0.0.0.0 --port 8443 \
  --user me --password secret --tls-cert cert.pem --tls-key key.pem

# Phone connects to https://<your-lan-ip>:8443/ (same network only)
```

---

## Commands Reference

| Command | Description | Example |
|---------|-------------|---------|
| `skref init` | Create a vault | `skref init --name work --encrypted` |
| `skref ls` | List vault contents | `skref ls photos --vault personal` |
| `skref put` | Store a file | `skref put doc.pdf --vault personal` |
| `skref open` | Decrypt + open in viewer | `skref open doc.pdf --vault personal` |
| `skref mount` | FUSE mount | `skref mount ~/vault --vault personal` |
| `skref serve` | WebDAV proxy for phones | `skref serve --vault personal --tailscale` |
| `skref share` | Share a file (Phase 6) | `skref share doc.pdf --to <fingerprint>` |
| `skref rekey` | Re-encrypt vault with new key | `skref rekey --vault personal` |

---

## Troubleshooting

### "gpg not found on PATH"

Install GPG:
- Linux: `sudo apt install gnupg` or `sudo pacman -S gnupg`
- macOS: `brew install gnupg`
- Windows: https://gpg4win.org/download.html

### "Encrypted vault requires a GPG key"

You need a PGP keypair. Either:
1. Run `capauth init` (creates identity + key)
2. Or generate manually: `gpg --full-generate-key`

### "FUSE not available"

```bash
pip install "skref[fuse]"
# Plus system package:
sudo apt install fuse3 libfuse3-dev     # Debian/Ubuntu
sudo pacman -S fuse3                     # Arch/Manjaro
```

### "Transport endpoint is not connected"

The FUSE mount crashed. Unmount and remount:

```bash
fusermount -u ~/vault
skref mount ~/vault --vault personal
```

### Mount point shows empty after reboot

Auto-mount isn't configured. See the systemd unit above, or add to your shell profile:

```bash
# In ~/.bashrc or ~/.zshrc
skref mount ~/vault --vault personal --no-foreground
```
