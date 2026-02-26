"""
SKRef — Sovereign Encrypted Reference Vaults.

FUSE-mounted, GPG-encrypted file vaults that sit on any backend
(local, Nextcloud, S3, Google Drive). Your CapAuth PGP key is the
only thing that unlocks them.

Tier 3 of the skcapstone storage model:
    Tier 1: ~/.skcapstone/sync/   (auth seeds, tiny, all devices)
    Tier 2: ~/.skcapstone/gtd/    (task lists, small JSON)
    Tier 3: skref vaults          (reference material, any backend)
"""

__version__ = "0.1.0"
