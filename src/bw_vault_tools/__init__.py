"""bw-vault-tools — local Bitwarden/Vaultwarden dedup and two-way sync over the bw CLI.

Planned modules (see docs/design/):
  bootstrap   self-install deps (venv + cryptography), re-exec, detect/version-gate bw
  bw_adapter  drive bw CLI via isolated BITWARDENCLI_APPDATA_DIR + BW_SESSION profiles
  identity    URI normalization, content fingerprint, cross-vault item matching (from
              bitwarden-vault-cleanup, MIT)
  plan        typed ops + safe-vs-destructive classification incl. passkey/SSH guard
  merge       3-way merge (sync only)
  snapshot    load/save encrypted last-synced snapshot S (sync only)
  keyprovider passphrase (cryptography) default + TPM2 (systemd-creds) hook
  checkpoint  encrypted staged snapshots + op journal + undo
  tmpfs       /dev/shm staging + shred-on-exit
  cli_dedup   bw-dedup entrypoint
  cli_sync    bw-sync entrypoint
"""

__version__ = "0.0.0"
