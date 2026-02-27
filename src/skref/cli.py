"""
SKRef CLI — manage and mount encrypted vaults.

Usage:
    skref init                         # Create default vault config
    skref ls [--vault NAME] [PATH]     # List vault contents
    skref put FILE [--vault NAME]      # Encrypt and store a file
    skref open PATH [--vault NAME]     # Decrypt to tmpfs and open
    skref mount MOUNTPOINT [--vault N] # FUSE mount (the good stuff)
"""

from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .backends.local import LocalBackend
from .backends.nextcloud import NextcloudBackend
from .config import load_config, save_config
from .models import BackendType, VaultConfig
from .vault import Vault

console = Console()
logger = logging.getLogger("skref.cli")


def _resolve_vault(vault_name: str | None) -> Vault:
    """Load config and build a Vault instance.

    Args:
        vault_name: Vault name from --vault flag, or None for default.

    Returns:
        Ready-to-use Vault.
    """
    cfg = load_config()
    name = vault_name or cfg.default_vault

    if name not in cfg.vaults:
        console.print(f"[red]Vault '{name}' not found.[/]")
        console.print(f"  Available: {', '.join(cfg.vaults.keys()) or '(none)'}")
        console.print("  Run [cyan]skref init[/] to create one.")
        sys.exit(1)

    vcfg = cfg.vaults[name]

    if vcfg.backend == BackendType.LOCAL:
        backend = LocalBackend(Path(vcfg.path))
    elif vcfg.backend == BackendType.NEXTCLOUD:
        import os

        url = vcfg.url or os.environ.get("SKREF_NEXTCLOUD_URL", "")
        user = os.environ.get("SKREF_NEXTCLOUD_USER", "")
        password = os.environ.get("SKREF_NEXTCLOUD_PASS", "")
        vault_path = os.environ.get("SKREF_NEXTCLOUD_PATH", vcfg.path or "/skref/")

        missing = [k for k, v in [("URL", url), ("user", user), ("password", password)] if not v]
        if missing:
            console.print(
                f"[red]Nextcloud backend requires: {', '.join(missing)}.[/]\n"
                "  Set SKREF_NEXTCLOUD_URL, SKREF_NEXTCLOUD_USER, SKREF_NEXTCLOUD_PASS."
            )
            sys.exit(1)

        try:
            backend = NextcloudBackend(
                url=url, username=user, password=password, vault_path=vault_path
            )
        except ValueError as exc:
            console.print(f"[red]Nextcloud backend error:[/] {exc}")
            sys.exit(1)
    else:
        console.print(f"[red]Backend '{vcfg.backend.value}' not yet implemented.[/]")
        sys.exit(1)

    return Vault(config=vcfg, backend=backend)


@click.group()
@click.version_option(version="0.1.0", prog_name="skref")
def main():
    """SKRef — sovereign encrypted reference vaults.

    FUSE-mounted, GPG-encrypted file storage. Your CapAuth PGP key
    unlocks your vaults. Any backend: local, Nextcloud, S3.
    """


@main.command()
@click.option("--name", default="personal", help="Vault name.")
@click.option("--path", default="~/.skcapstone/vaults/personal", help="Local storage path.")
@click.option("--encrypted/--no-encrypted", default=True, help="Encrypt files at rest.")
def init(name: str, path: str, encrypted: bool):
    """Initialize a vault and write config.

    Creates ~/.skcapstone/vaults.yaml with a default vault.
    """
    from .crypto import detect_key

    cfg = load_config()

    if name in cfg.vaults:
        if not click.confirm(f"Vault '{name}' already exists. Overwrite config?", default=False):
            console.print("[yellow]Aborted.[/]")
            return

    vcfg = VaultConfig(
        name=name,
        backend=BackendType.LOCAL,
        encrypted=encrypted,
        path=path,
    )

    if encrypted:
        fp = detect_key()
        if fp:
            console.print(f"  GPG key: [cyan]{fp[:16]}...[/]")
        else:
            console.print(
                "[yellow]Warning: No GPG key found.[/] "
                "Run 'capauth init' or generate a PGP keypair."
            )

    storage = Path(path).expanduser()
    storage.mkdir(parents=True, exist_ok=True)

    cfg.vaults[name] = vcfg
    config_path = save_config(cfg)

    console.print()
    console.print(
        Panel(
            f"Vault [bold]{name}[/] initialized\n\n"
            f"  Backend:   local\n"
            f"  Path:      {storage}\n"
            f"  Encrypted: {'yes' if encrypted else 'no'}\n"
            f"  Config:    {config_path}",
            title="SKRef",
            border_style="green",
        )
    )
    console.print()
    console.print("  [dim]Next:[/]")
    console.print(f"    skref put myfile.pdf --vault {name}")
    console.print(f"    skref ls --vault {name}")
    console.print(f"    skref mount ~/vault --vault {name}")


@main.command("ls")
@click.argument("path", default="")
@click.option("--vault", "vault_name", default=None, help="Vault name.")
def ls_cmd(path: str, vault_name: str | None):
    """List files in a vault directory."""
    vault = _resolve_vault(vault_name)
    entries = vault.list_dir(path)

    if not entries:
        console.print(f"\n  [dim]Empty: {path or '(root)'}[/]\n")
        return

    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    table.add_column("Type", width=4)
    table.add_column("Name")
    table.add_column("Size", justify="right")

    for e in entries:
        kind = "[blue]dir[/]" if e.is_dir else "[dim]file[/]"
        size = _human_size(e.size) if not e.is_dir else ""
        table.add_column if False else None  # noqa — keep linter happy
        table.add_row(kind, e.name, size)

    console.print()
    console.print(table)
    console.print()


@main.command()
@click.argument("filepath", type=click.Path(exists=True))
@click.option("--vault", "vault_name", default=None, help="Vault name.")
@click.option("--dest", default=None, help="Destination path inside the vault.")
def put(filepath: str, vault_name: str | None, dest: str | None):
    """Encrypt and store a file in a vault."""
    vault = _resolve_vault(vault_name)
    src = Path(filepath)
    rel_dest = dest or src.name

    data = src.read_bytes()
    vault.write(rel_dest, data)

    enc_label = " (encrypted)" if vault.encrypted else ""
    console.print(f"  [green]Stored:[/] {src.name} → {rel_dest}{enc_label}")


@main.command("open")
@click.argument("path")
@click.option("--vault", "vault_name", default=None, help="Vault name.")
def open_cmd(path: str, vault_name: str | None):
    """Decrypt a vault file to tmpfs and open it.

    The plaintext file is written to a temporary directory (tmpfs on
    Linux) and opened with the system default viewer. Cleaned up
    after the viewer closes.
    """
    vault = _resolve_vault(vault_name)

    if not vault.exists(path):
        console.print(f"[red]Not found:[/] {path}")
        sys.exit(1)

    data = vault.read(path)
    filename = Path(path).name

    tmpdir = _get_tmpfs_dir()
    tmp_path = Path(tmpdir) / filename
    tmp_path.write_bytes(data)

    try:
        console.print(f"  [dim]Opening {filename} (decrypted to tmpfs)...[/]")
        _xdg_open(str(tmp_path))
    finally:
        tmp_path.unlink(missing_ok=True)


@main.command()
@click.argument("mountpoint", type=click.Path())
@click.option("--vault", "vault_name", default=None, help="Vault name.")
@click.option("--foreground/--no-foreground", default=True, help="Run in foreground.")
def mount(mountpoint: str, vault_name: str | None, foreground: bool):
    """FUSE-mount a vault — browse encrypted files as a normal folder.

    Files are decrypted on read and encrypted on write. The mountpoint
    never has plaintext on real disk.

    Requires: pip install skref[fuse]
    Linux: sudo apt install fuse3 libfuse3-dev
    macOS: install macFUSE
    """
    from .fuse_mount import check_fuse_available, mount_vault

    if not check_fuse_available():
        console.print(
            Panel(
                "[bold red]FUSE not available.[/]\n\n"
                "Install the FUSE dependency:\n"
                "  [cyan]pip install skref[fuse][/]\n\n"
                "System packages:\n"
                "  Linux:  sudo apt install fuse3 libfuse3-dev\n"
                "  Arch:   sudo pacman -S fuse3\n"
                "  macOS:  brew install macfuse (or osxfuse.github.io)",
                title="FUSE required",
                border_style="red",
            )
        )
        sys.exit(1)

    vault = _resolve_vault(vault_name)

    console.print()
    console.print(
        Panel(
            f"Mounting vault [bold]{vault.config.name}[/]\n\n"
            f"  Mountpoint: {mountpoint}\n"
            f"  Encrypted:  {'yes' if vault.encrypted else 'no'}\n"
            f"  Backend:    {vault.config.backend.value}\n\n"
            "  [dim]Files decrypt on read, encrypt on write.\n"
            "  Ctrl-C or 'umount' to stop.[/]",
            title="SKRef FUSE",
            border_style="green",
        )
    )
    console.print()

    mount_vault(vault, mountpoint, foreground=foreground)


@main.command()
@click.option("--vault", "vault_name", default=None, help="Vault name.")
@click.option("--host", default="127.0.0.1", help="Bind address.")
@click.option("--port", default=8443, type=int, help="Port number.")
@click.option("--user", default=None, help="Basic auth username (or SKREF_WEBDAV_USER env).")
@click.option("--password", default=None, help="Basic auth password (or SKREF_WEBDAV_PASS env).")
@click.option("--tailscale/--no-tailscale", default=False,
              help="Expose via Tailscale Funnel (auto TLS, global FQDN).")
def serve(vault_name: str | None, host: str, port: int, user: str | None,
          password: str | None, tailscale: bool):
    """Start a WebDAV proxy server for a vault.

    Access your encrypted vault from any device with a WebDAV client
    (phone, tablet, remote desktop). Files decrypt on read, encrypt on write.

    With --tailscale: exposes the server via Tailscale Funnel with a valid
    HTTPS certificate and a globally-routable FQDN. No port forwarding,
    no self-signed certs, no dynamic DNS.
    """
    from . import tailscale as ts_mod

    vault = _resolve_vault(vault_name)

    if tailscale:
        if not ts_mod.is_installed():
            console.print(
                Panel(
                    "[bold red]Tailscale not found.[/]\n\n"
                    + ts_mod.get_install_hint(),
                    title="Tailscale required for --tailscale",
                    border_style="red",
                )
            )
            sys.exit(1)

        status = ts_mod.get_status()
        if not status:
            console.print("[red]Tailscale is installed but not running.[/]")
            console.print("  Start with: [cyan]sudo tailscale up[/]")
            sys.exit(1)

        fqdn = status.fqdn
        console.print()
        console.print(
            Panel(
                f"Tailscale detected: [bold cyan]{fqdn}[/]\n"
                f"  Tailnet IP: {status.ip4}\n\n"
                f"Setting up Tailscale Serve + Funnel...\n"
                f"  Local:  http://127.0.0.1:{port}\n"
                f"  Public: [bold green]https://{fqdn}:443/[/]\n\n"
                "  [dim]Valid Let's Encrypt TLS certificate\n"
                "  No port forwarding required\n"
                "  Accessible from anywhere[/]",
                title="SKRef + Tailscale Funnel",
                border_style="green",
            )
        )
        console.print()

        ts_mod.serve_background(port)
        ts_mod.enable_funnel(port)

        console.print(f"  [bold]Phone setup:[/]")
        console.print(f"    WebDAV URL: [cyan]https://{fqdn}:443/[/]")
        if user:
            console.print(f"    Username:   {user}")
        console.print()

        host = "127.0.0.1"
    else:
        console.print()
        console.print(
            Panel(
                f"Starting WebDAV proxy for vault [bold]{vault.config.name}[/]\n\n"
                f"  Bind:      {host}:{port}\n"
                f"  Encrypted: {'yes' if vault.encrypted else 'no'}\n"
                f"  Backend:   {vault.config.backend.value}\n\n"
                "  [dim]Tip: use --tailscale for global HTTPS access\n"
                "  via Tailscale Funnel (no port forwarding needed)[/]",
                title="SKRef WebDAV Proxy",
                border_style="green",
            )
        )
        console.print()

    console.print(f"  [dim]WebDAV proxy running on http://{host}:{port}/[/]")
    console.print(f"  [dim]Press Ctrl-C to stop.[/]\n")

    # Phase 4 TODO: replace with actual WebDAV server (webdav_proxy.py)
    console.print(
        "[yellow]WebDAV server not yet implemented (Phase 4).[/]\n"
        "  The Tailscale integration is ready — the proxy is the next piece.\n"
        "  See docs/phases/PHASE-4.md for the full agent spec."
    )


@main.command()
@click.option("--remote/--no-remote", default=None,
              help="Jump straight to remote access setup.")
@click.option("--non-interactive", is_flag=True, help="Use defaults (for CI/scripting).")
def setup(remote: bool | None, non_interactive: bool):
    """Interactive setup wizard — configure vaults, Tailscale, and remote access.

    This is automatically called during `skcapstone install`.
    Run it again to reconfigure or add remote access later.
    """
    from .setup_wizard import run_setup_wizard

    run_setup_wizard(non_interactive=non_interactive)


@main.command("save-auth-key")
@click.argument("key")
def save_auth_key_cmd(key: str):
    """Encrypt and save a Tailscale auth key to the sync folder.

    Use this if auto-generation failed and you created a reusable
    auth key manually at https://login.tailscale.com/admin/settings/keys.

    The encrypted key syncs to other devices so they can auto-join
    your tailnet without a browser.
    """
    from . import tailscale as ts_mod

    saved = ts_mod.save_auth_key(key)
    if saved:
        console.print(f"  [green]Auth key encrypted & saved[/] → {saved}")
        console.print("  [dim]Will sync to other devices via Syncthing.[/]")
    else:
        console.print(
            "[red]Failed to save auth key.[/]\n"
            "  Ensure your CapAuth GPG key is set up:\n"
            "    capauth init\n"
            "  Then try again."
        )


@main.command()
@click.option("--vault", "vault_name", default=None, help="Vault name.")
@click.option("--url", default=None, help="Nextcloud URL (or SKREF_NEXTCLOUD_URL).")
@click.option("--user", default=None, help="Nextcloud username (or SKREF_NEXTCLOUD_USER).")
@click.option("--password", default=None, help="Nextcloud password (or SKREF_NEXTCLOUD_PASS).")
@click.option("--path", "vault_path", default=None,
              help="Remote vault path (or SKREF_NEXTCLOUD_PATH, default /skref/).")
def sync(vault_name: str | None, url: str | None, user: str | None,
         password: str | None, vault_path: str | None):
    """Sync local vault with Nextcloud.

    Compares what exists locally vs. remotely and reports the sync state.
    Use 'skref remote pull' to fetch specific refs.
    """
    import os

    url = url or os.environ.get("SKREF_NEXTCLOUD_URL", "")
    user = user or os.environ.get("SKREF_NEXTCLOUD_USER", "")
    password = password or os.environ.get("SKREF_NEXTCLOUD_PASS", "")
    vault_path = vault_path or os.environ.get("SKREF_NEXTCLOUD_PATH", "/skref/")

    if not all([url, user, password]):
        console.print(
            "[red]Nextcloud credentials required.[/]\n"
            "  Set SKREF_NEXTCLOUD_URL, SKREF_NEXTCLOUD_USER, SKREF_NEXTCLOUD_PASS\n"
            "  or pass --url, --user, --password."
        )
        sys.exit(1)

    try:
        nc = NextcloudBackend(url=url, username=user, password=password,
                              vault_path=vault_path)
    except ValueError as exc:
        console.print(f"[red]Nextcloud backend error:[/] {exc}")
        sys.exit(1)

    console.print(f"\n  Checking connection to [cyan]{url}[/]...")
    health = nc.health()
    if not health["ok"]:
        console.print(f"  [red]Connection failed.[/] {health.get('error', '')}")
        sys.exit(1)

    console.print(f"  [green]Connected[/] as {user}")
    status = nc.sync_status()
    console.print(
        Panel(
            f"Remote refs:  {status['remote_ref_count']}\n"
            f"Sync status:  {status['status']}",
            title=f"SKRef Sync — {vault_path}",
            border_style="green",
        )
    )
    console.print()


@main.group()
def remote():
    """Interact with the Nextcloud remote vault."""


@remote.command("list")
@click.option("--url", default=None, help="Nextcloud URL.")
@click.option("--user", default=None, help="Nextcloud username.")
@click.option("--password", default=None, help="Nextcloud password.")
@click.option("--path", "vault_path", default=None, help="Remote vault path.")
@click.option("--prefix", default="", help="Filter by path prefix.")
def remote_list(url: str | None, user: str | None, password: str | None,
                vault_path: str | None, prefix: str):
    """List refs on Nextcloud."""
    import os

    url = url or os.environ.get("SKREF_NEXTCLOUD_URL", "")
    user = user or os.environ.get("SKREF_NEXTCLOUD_USER", "")
    password = password or os.environ.get("SKREF_NEXTCLOUD_PASS", "")
    vault_path = vault_path or os.environ.get("SKREF_NEXTCLOUD_PATH", "/skref/")

    if not all([url, user, password]):
        console.print("[red]Nextcloud credentials required.[/]")
        sys.exit(1)

    try:
        nc = NextcloudBackend(url=url, username=user, password=password,
                              vault_path=vault_path)
    except ValueError as exc:
        console.print(f"[red]{exc}[/]")
        sys.exit(1)

    refs = nc.list_refs(prefix=prefix)

    if not refs:
        console.print(f"\n  [dim]No refs found under {vault_path}{prefix}[/]\n")
        return

    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    table.add_column("Ref ID")
    table.add_column("Title")
    table.add_column("Tags")
    table.add_column("Size", justify="right")

    for r in refs:
        tags = ", ".join(r.get("tags", [])) if r.get("tags") else ""
        table.add_row(
            r.get("ref_id", ""),
            r.get("title", "[dim]—[/]"),
            tags or "[dim]—[/]",
            _human_size(r.get("size", 0)),
        )

    console.print()
    console.print(table)
    console.print()


@remote.command("pull")
@click.argument("ref_id")
@click.option("--url", default=None, help="Nextcloud URL.")
@click.option("--user", default=None, help="Nextcloud username.")
@click.option("--password", default=None, help="Nextcloud password.")
@click.option("--path", "vault_path", default=None, help="Remote vault path.")
@click.option("--dest", default=None,
              help="Local destination path. Defaults to current directory.")
def remote_pull(ref_id: str, url: str | None, user: str | None, password: str | None,
                vault_path: str | None, dest: str | None):
    """Pull a specific ref from Nextcloud and save it locally.

    The file is downloaded as-is (still encrypted if the vault is encrypted).
    Use 'skref open' to decrypt and open a locally stored ref.
    """
    import os

    url = url or os.environ.get("SKREF_NEXTCLOUD_URL", "")
    user = user or os.environ.get("SKREF_NEXTCLOUD_USER", "")
    password = password or os.environ.get("SKREF_NEXTCLOUD_PASS", "")
    vault_path = vault_path or os.environ.get("SKREF_NEXTCLOUD_PATH", "/skref/")

    if not all([url, user, password]):
        console.print("[red]Nextcloud credentials required.[/]")
        sys.exit(1)

    try:
        nc = NextcloudBackend(url=url, username=user, password=password,
                              vault_path=vault_path)
    except ValueError as exc:
        console.print(f"[red]{exc}[/]")
        sys.exit(1)

    try:
        content, metadata = nc.get_ref(ref_id)
    except FileNotFoundError:
        console.print(f"[red]Ref not found on Nextcloud:[/] {ref_id}")
        sys.exit(1)

    filename = Path(ref_id).name
    out_path = Path(dest or ".") / filename
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(content)

    title = metadata.get("title", "")
    console.print(
        f"  [green]Pulled:[/] {ref_id} → {out_path}"
        + (f"  [dim]({title})[/]" if title else "")
    )


def _human_size(n: int) -> str:
    """Format bytes as human-readable size."""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _get_tmpfs_dir() -> str:
    """Get a tmpfs-backed temp directory (Linux) or regular temp dir."""
    if platform.system() == "Linux":
        run_user = f"/run/user/{os.getuid()}"
        if os.path.isdir(run_user):
            d = os.path.join(run_user, "skref-tmp")
            os.makedirs(d, exist_ok=True)
            return d
    return tempfile.mkdtemp(prefix="skref-")


def _xdg_open(filepath: str) -> None:
    """Open a file with the system's default application."""
    system = platform.system()
    if system == "Linux":
        opener = "xdg-open"
    elif system == "Darwin":
        opener = "open"
    elif system == "Windows":
        os.startfile(filepath)  # type: ignore[attr-defined]
        return
    else:
        opener = "xdg-open"

    try:
        subprocess.run([opener, filepath], check=False, timeout=300)
    except FileNotFoundError:
        console.print(f"[yellow]Could not find '{opener}'. Open manually: {filepath}[/]")
