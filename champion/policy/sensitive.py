"""Paths that are blocked at every access level.

Ported from mChampion ``policy/sensitive.py`` and extended. A path is blocked
when any part of it looks like a secret (``~/.ssh/...``, ``.env``,
``credentials.json``, ``*.pem`` ...), when it is under a system folder such as
``/proc``, ``/sys`` or ``/dev``, or (for writing) under an operating-system
folder such as ``/etc`` or ``/usr``.

Files ending in ``.key`` are not blocked by name, because LS-DYNA models use
that extension; ``filesystem_read`` checks their content instead (a private
key is blocked, an LS-DYNA keyword file is allowed).

The checks are on names only and are deliberately broad: a false positive
("tokenizer.py" is blocked) is safer than a leaked key.

Champion's own folder ``.champion`` (undo backups, later the script folder
and its trust store, step 4c) is blocked too: no agent tool may read or
change the data Champion uses to undo or to trust things.
"""

from __future__ import annotations

from pathlib import PurePosixPath

# Never read or written, at any level.
SYSTEM_ROOTS = ("/proc", "/sys", "/dev", "/run/secrets", "/var/run/secrets", "/root")
SYSTEM_FILES = {"/etc/shadow", "/etc/gshadow", "/etc/sudoers", "/etc/master.passwd", "/etc/security/opasswd"}
# Never written (reading is allowed at levels 2.1 / 2.2).
OS_WRITE_ROOTS = ("/bin", "/boot", "/etc", "/lib", "/lib32", "/lib64", "/opt", "/sbin", "/usr", "/var")

SECRET_NAMES = {
    ".ssh", ".aws", ".azure", ".gnupg", ".gpg", ".kube", ".docker", ".password-store", "gcloud",
    "keyrings", ".netrc", ".npmrc", ".pypirc", ".pgpass", ".my.cnf", ".git-credentials",
    ".databrickscfg", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "authorized_keys", "known_hosts",
    "secrets", "credentials", "certs", "certificates",
}
INTERNAL_NAMES = {".champion"}
SECRET_WORDS = ("secret", "credential", "token", "password", "passwd", "private")
SECRET_SUFFIXES = (
    ".pem", ".p12", ".pfx", ".crt", ".cer", ".der", ".jks", ".keystore", ".kdbx", ".gpg", ".ppk", ".ovpn",
    ".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite-wal", ".sqlite-shm",
    ".sqlite3", ".sqlite3-wal", ".sqlite3-shm",
)


def _under(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


def name_reason(name: str) -> str | None:
    """Why a single path component is blocked, or None."""
    lower = name.lower()
    if lower in INTERNAL_NAMES:
        return f"'{name}' is Champion's own folder (undo backups, scripts, trust store)"
    if lower in SECRET_NAMES:
        return f"'{name}' is a secret or credential location"
    if lower == ".env" or lower.startswith(".env.") or lower.endswith(".env"):
        return f"'{name}' is an environment (.env) file"
    if any(word in lower for word in SECRET_WORDS):
        return f"'{name}' looks like a secret, token, password or private file"
    if lower.endswith(SECRET_SUFFIXES):
        return f"'{name}' is a key, certificate or database file"
    return None


def sensitive_reason(path: str | PurePosixPath, *, write: bool = False) -> str | None:
    """Why an absolute, normalised path is blocked at every level, or None."""
    text = str(path)
    for root in SYSTEM_ROOTS:
        if _under(text, root):
            return f"system folder {root} is always blocked"
    if text in SYSTEM_FILES:
        return f"{text} is a protected system file"
    if write:
        for root in OS_WRITE_ROOTS:
            if _under(text, root):
                return f"operating-system folder {root} is never written"
    for part in PurePosixPath(text).parts[1:]:
        reason = name_reason(part)
        if reason:
            return reason + "; secret files are always blocked"
    return None
