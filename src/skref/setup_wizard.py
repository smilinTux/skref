"""
Interactive setup wizard for skref — called during skcapstone install.

Full guided workflow:

  1. Datastore question: "Store vault files on this machine?"
  2. Remote access question: "Access from phone / other computers?"
  3. Tailscale setup (if yes to remote):
     a. Check for synced auth key from another device → auto-join
     b. If no synced key: install Tailscale, open browser login
     c. After auth: generate reusable auth key, GPG-encrypt, save to sync
     d. Enable Serve + Funnel
  4. Create default "personal" vault
  5. Publish to vault registry

On second+ devices, the flow is:
  → detect synced tailscale.key.gpg in ~/.skcapstone/sync/
  → decrypt with CapAuth PGP key
  → `tailscale up --auth-key=<key>` (no browser needed)
  → done

KISS: the user never sees an auth key, never visits the admin console,
never opens a port. They answer yes/no and the wizard handles everything.
"""

from __future__ import annotations

import platform
import socket
import time
import uuid
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import tailscale
from .config import load_config, save_config
from .models import (
    BackendType,
    DeviceConfig,
    DeviceRole,
    ServeConfig,
    SkrefConfig,
    VaultConfig,
    VaultEndpoint,
)

console = Console()


# ---------------------------------------------------------------------------
# Tailscale sub-flow
# ---------------------------------------------------------------------------

def _tailscale_flow(non_interactive: bool = False) -> tuple[bool, Optional[tailscale.TailscaleStatus]]:
    """Walk the user through the entire Tailscale setup.

    Steps:
      1. Check for synced auth key → auto-join if found
      2. Check if Tailscale is already installed and authenticated
      3. Offer to install Tailscale automatically
      4. Authenticate (browser or auth key)
      5. Generate & save a reusable auth key for future devices
      6. Enable Funnel

    Args:
        non_interactive: Skip prompts, use defaults.

    Returns:
        Tuple of (funnel_enabled, TailscaleStatus or None).
    """
    console.print()
    console.print(
        Panel(
            "[bold]Tailscale Setup[/]\n\n"
            "Tailscale creates a private mesh network between your devices.\n"
            "It gives this machine a unique address reachable from anywhere:\n"
            "  [cyan]https://your-machine.tail1234.ts.net[/]\n\n"
            "Your phone, laptop, or any device on your tailnet can reach\n"
            "your encrypted vaults without port forwarding or VPNs.\n\n"
            "[dim]Free for personal use — up to 100 devices.[/]",
            title="What is Tailscale?",
            border_style="blue",
        )
    )
    console.print()

    # --- Step 1: Check for synced auth key (second+ device) ---
    synced_key = None
    if tailscale.has_synced_auth_key():
        console.print("  [green]Found synced auth key[/] from another device!")
        console.print("  [dim]Decrypting with your CapAuth PGP key...[/]")
        synced_key = tailscale.load_auth_key()
        if synced_key:
            console.print("  [green]Auth key decrypted.[/] Will join your tailnet automatically.")
        else:
            console.print("  [yellow]Could not decrypt auth key[/] — will do browser login instead.")
    else:
        console.print("  [dim]No synced auth key found — this appears to be your first device.[/]")

    # --- Step 2: Check if already installed ---
    if tailscale.is_installed():
        console.print("  [green]Tailscale is already installed.[/]")
    else:
        console.print()
        console.print("  Tailscale is not installed on this machine.")
        if not non_interactive:
            do_install = click.confirm(
                "  Install Tailscale now? (recommended)",
                default=True,
            )
        else:
            do_install = True

        if do_install:
            console.print()
            system = platform.system()
            if system == "Linux":
                console.print("  [dim]Running: curl -fsSL https://tailscale.com/install.sh | sh[/]")
            elif system == "Darwin":
                console.print("  [dim]Running: brew install tailscale[/]")
            elif system == "Windows":
                console.print("  [dim]Running: winget install Tailscale.Tailscale[/]")

            console.print("  [dim]Installing... (this may take a minute)[/]")
            success = tailscale.auto_install()

            if success:
                console.print("  [green]Tailscale installed![/]")
            else:
                console.print("  [red]Auto-install failed.[/]")
                console.print(f"  {tailscale.get_install_hint()}")
                console.print("  Install manually, then re-run setup.")
                return False, None
        else:
            console.print(f"  {tailscale.get_install_hint()}")
            console.print("  You can re-run setup later after installing.")
            return False, None

    # --- Step 3: Authenticate ---
    ts_status = tailscale.get_status()

    if ts_status and ts_status.running:
        console.print(f"  [green]Already connected to tailnet:[/] {ts_status.fqdn}")
    else:
        console.print()
        if synced_key:
            console.print("  [dim]Joining your tailnet with synced auth key...[/]")
            auth_ok = tailscale.authenticate(auth_key=synced_key)
        else:
            console.print(
                "  [bold]Time to connect to your Tailscale account.[/]\n"
                "  A browser window will open for you to log in.\n"
                "  [dim](Google, GitHub, Microsoft, or email — whichever you prefer)[/]"
            )
            console.print()
            if not non_interactive:
                click.confirm("  Ready to open your browser?", default=True)
            console.print("  [dim]Opening browser for login...[/]")
            auth_ok = tailscale.authenticate()

        if auth_ok:
            console.print("  [green]Connected![/]")
            time.sleep(1)
            ts_status = tailscale.get_status()
        else:
            console.print("  [yellow]Authentication did not complete.[/]")
            console.print("  You may need to run [cyan]sudo tailscale up[/] manually.")
            console.print("  Re-run setup afterward.")
            return False, None

    if not ts_status:
        console.print("  [yellow]Could not get Tailscale status.[/]")
        return False, None

    console.print(f"  [bold]Your address:[/] [cyan]{ts_status.fqdn}[/]")
    console.print(f"  [bold]Private IP:[/]   {ts_status.ip4}")

    # --- Step 4: Save auth key for future devices ---
    if not tailscale.has_synced_auth_key():
        console.print()
        console.print("  [dim]Generating auth key so your next device can join automatically...[/]")
        new_key = tailscale.generate_auth_key()
        if new_key:
            saved_path = tailscale.save_auth_key(new_key)
            if saved_path:
                console.print(
                    f"  [green]Auth key encrypted & saved[/] → {saved_path}\n"
                    "  [dim]This file will sync to other devices via Syncthing.\n"
                    "  Your next device will auto-join — no browser needed.[/]"
                )
            else:
                console.print(
                    "  [yellow]Could not encrypt auth key[/] — GPG key may not be set up yet.\n"
                    "  You can generate one later from the Tailscale admin console:\n"
                    f"  {tailscale.get_admin_console_url()}"
                )
        else:
            console.print(
                "  [yellow]Could not auto-generate auth key[/] (Tailscale API not available).\n"
                "  You can create a reusable auth key manually:\n"
                f"  1. Go to [cyan]{tailscale.get_admin_console_url()}[/]\n"
                "  2. Generate a [bold]reusable[/] auth key\n"
                "  3. Run: [cyan]skref save-auth-key <your-key>[/]"
            )

    # --- Step 5: Enable Funnel ---
    console.print()
    if not non_interactive:
        enable_funnel = click.confirm(
            "  Enable Tailscale Funnel?\n"
            "  (Makes vaults accessible from anywhere via HTTPS — even off your home network)",
            default=True,
        )
    else:
        enable_funnel = True

    if enable_funnel:
        console.print("  [dim]Configuring Serve + Funnel...[/]", end=" ")
        serve_ok = tailscale.serve_background(8443)
        funnel_ok = tailscale.enable_funnel(8443) if serve_ok else False

        if funnel_ok:
            console.print("[green]done[/]")
            console.print(
                f"  [bold]Phone URL:[/] [cyan]https://{ts_status.fqdn}:443/[/]\n"
                "  [dim]Connect with any WebDAV app (FolderSync, Solid Explorer, etc.)[/]"
            )
            return True, ts_status
        else:
            console.print("[yellow]failed[/]")
            console.print(
                "  [dim]Funnel may need admin approval in your Tailscale account.\n"
                "  Go to admin console → DNS → Enable Funnel.\n"
                "  Then run: [cyan]skref serve --tailscale[/][/]"
            )
            return False, ts_status
    else:
        console.print(
            "  [dim]Funnel skipped.[/] Vaults are still accessible on your private tailnet.\n"
            "  You can enable it later: [cyan]skref serve --tailscale[/]"
        )
        return False, ts_status


# ---------------------------------------------------------------------------
# Main wizard
# ---------------------------------------------------------------------------

def run_setup_wizard(
    agent_name: str = "",
    agent_home: Optional[Path] = None,
    non_interactive: bool = False,
) -> SkrefConfig:
    """Run the interactive vault setup wizard.

    Args:
        agent_name: Name of the sovereign agent (from skcapstone install).
        agent_home: Agent home directory.
        non_interactive: If True, use defaults without prompting.

    Returns:
        Configured SkrefConfig.
    """
    cfg = load_config()

    hostname = socket.gethostname()
    device_id = cfg.device.device_id or str(uuid.uuid4())[:8]

    console.print()
    console.print(
        Panel(
            "[bold]Sovereign Reference Vault Setup[/]\n\n"
            "SKRef gives you encrypted file vaults accessible from any device.\n"
            "Files are GPG-encrypted at rest — your CapAuth key unlocks them.\n"
            "Mount as a folder (FUSE), access from phone (WebDAV), or CLI.",
            title="SKRef",
            border_style="cyan",
        )
    )
    console.print()

    # --- Question 1: Is this a datastore? ---
    if non_interactive:
        is_datastore = True
    else:
        console.print(f"  [bold]Device:[/] {hostname}")
        console.print()
        is_datastore = click.confirm(
            "  Use this computer as a vault datastore?\n"
            "  (Store encrypted files on this machine's disk)",
            default=True,
        )

    console.print()
    if is_datastore:
        console.print("  [green]Datastore enabled.[/] Vault files will live on this machine.")
    else:
        console.print(
            "  [dim]Client only.[/] This device will access vaults on other machines.\n"
            "  You can still use `skref put` and `skref open` — files are fetched remotely."
        )

    # --- Question 2: Remote access? ---
    enable_remote = False
    enable_funnel = False
    ts_status = None

    if not non_interactive:
        console.print()
        enable_remote = click.confirm(
            "  Enable remote access from your phone or other computers?\n"
            "  (Recommended — uses Tailscale, free encrypted mesh network)",
            default=True,
        )
    else:
        enable_remote = is_datastore

    if enable_remote:
        enable_funnel, ts_status = _tailscale_flow(non_interactive=non_interactive)
    else:
        console.print(
            "\n  [dim]Remote access skipped.[/] You can enable it later:\n"
            "    [cyan]skref setup --remote[/]"
        )

    # --- Build config ---
    device = DeviceConfig(
        hostname=hostname,
        device_id=device_id,
        is_datastore=is_datastore,
        tailscale_fqdn=ts_status.fqdn if ts_status else "",
        tailscale_ip=ts_status.ip4 if ts_status else "",
        funnel_enabled=enable_funnel,
        funnel_port=8443,
    )
    cfg.device = device

    # --- Create default vault if datastore ---
    if is_datastore and "personal" not in cfg.vaults:
        vault_path = "~/.skcapstone/vaults/personal"
        Path(vault_path).expanduser().mkdir(parents=True, exist_ok=True)

        endpoints = [
            VaultEndpoint(kind="local", url=vault_path, device=hostname, priority=10),
        ]
        if ts_status and ts_status.ip4:
            endpoints.append(VaultEndpoint(
                kind="tailnet",
                url=f"http://{ts_status.ip4}:8443/personal/",
                device=hostname,
                priority=20,
            ))
        if enable_funnel and ts_status:
            endpoints.append(VaultEndpoint(
                kind="funnel",
                url=f"https://{ts_status.fqdn}/personal/",
                device=hostname,
                priority=30,
            ))

        cfg.vaults["personal"] = VaultConfig(
            name="personal",
            backend=BackendType.LOCAL,
            encrypted=True,
            path=vault_path,
            origin_device=hostname,
            role=DeviceRole.ORIGIN,
            endpoints=endpoints,
            published=enable_remote,
        )

    serve = ServeConfig(
        tailscale_funnel=enable_funnel,
        tailscale_serve=enable_remote,
    )
    cfg.serve = serve

    # --- Save ---
    config_path = save_config(cfg)

    # --- Publish to registry if remote enabled ---
    if enable_remote and is_datastore:
        try:
            from .registry import publish_device
            publish_device(cfg)
            console.print("  [green]Published to vault registry.[/]")
        except Exception as exc:
            console.print(f"  [yellow]Registry publish failed: {exc}[/]")

    # --- Summary ---
    console.print()

    rows = []
    for name, v in cfg.vaults.items():
        access = "local"
        if v.published and enable_funnel:
            access = "local + tailnet + funnel"
        elif v.published:
            access = "local + tailnet"
        rows.append((name, v.backend.value, "yes" if v.encrypted else "no", access))

    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    table.add_column("Vault")
    table.add_column("Backend")
    table.add_column("Encrypted")
    table.add_column("Access")
    for row in rows:
        table.add_row(*row)

    summary_lines = [
        f"  Device:    [cyan]{hostname}[/] ({device_id})",
        f"  Datastore: {'[green]yes[/]' if is_datastore else '[dim]no (client)[/]'}",
    ]
    if ts_status:
        summary_lines.append(f"  Tailscale: [cyan]{ts_status.fqdn}[/]")
    if enable_funnel:
        summary_lines.append(f"  Funnel:    [green]enabled[/] (port 8443)")
    summary_lines.append(f"  Config:    {config_path}")

    console.print(Panel(
        "\n".join(summary_lines),
        title="SKRef Setup Complete",
        border_style="green",
    ))
    console.print()
    console.print(table)
    console.print()

    if is_datastore:
        console.print("  [bold]Next steps:[/]")
        console.print("    skref put myfile.pdf                — store a file")
        console.print("    skref mount ~/vault                 — FUSE mount")
        if enable_funnel and ts_status:
            console.print(
                "    skref serve --tailscale             — start WebDAV for phone"
            )
        console.print("    skref ls --all-devices              — see all vaults everywhere")
    else:
        console.print("  [bold]Next steps:[/]")
        console.print("    skref ls --all-devices              — discover vaults on other devices")
        console.print("    skref open <file> --vault <remote>  — open a remote file")

    console.print()
    return cfg
