"""Hidee's release signing key: make it once, then sign each release.

  python scripts/release_key.py new                 make the key (once); prints the public key for hidee/update.py
  python scripts/release_key.py sign <wheel>        write <wheel>.sig, the signature `hidee update` checks
  python scripts/release_key.py public              print the public key again

The private key is a 32-byte seed in ~/.config/hidee/release-signing.key (0600), or $HIDEE_RELEASE_KEY, with a copy
in the macOS login keychain (service "hidee-release-signing-key") that is used if the file is missing. It never goes
into the repository. Without it you cannot sign updates that installed copies accept, and anyone who has it can.
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hidee.ed25519 import public_key, sign  # noqa: E402
from hidee.update import signed_message  # noqa: E402

KEY = Path(os.environ.get("HIDEE_RELEASE_KEY", Path.home() / ".config" / "hidee" / "release-signing.key"))


KEYCHAIN = "hidee-release-signing-key"      # a copy in the macOS login keychain, in case the file is lost


def _from_keychain() -> str | None:
    import subprocess
    try:
        done = subprocess.run(["security", "find-generic-password", "-a", os.environ.get("USER", ""), "-s", KEYCHAIN,
                               "-w"], capture_output=True, text=True)
    except OSError:
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def load() -> bytes:
    try:
        text = KEY.read_text().strip()
    except FileNotFoundError:
        text = _from_keychain()
        if text is None:
            sys.exit(f"No release key at {KEY} or in the keychain. Make one with: python scripts/release_key.py new")
    seed = bytes.fromhex(text)
    if len(seed) != 32:
        sys.exit(f"{KEY} does not hold a 32-byte key.")
    return seed


def main(argv: list[str]) -> None:
    if argv[:1] == ["new"]:
        if KEY.exists():
            sys.exit(f"{KEY} already exists; it is not replaced. Installed copies trust only the key they know.")
        KEY.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(KEY, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(os.urandom(32).hex() + "\n")
        import subprocess
        seed = KEY.read_text().strip()
        kept = subprocess.run(["security", "add-generic-password", "-U", "-a", os.environ.get("USER", ""), "-s", KEYCHAIN,
                               "-l", "Hidee release signing key", "-w", seed], capture_output=True).returncode == 0
        print(f"Made {KEY} (readable only by you)" + (", with a copy in your login keychain." if kept else "."))
        print(f"Public key for hidee/update.py: {public_key(load()).hex()}")
    elif argv[:1] == ["public"]:
        print(public_key(load()).hex())
    elif argv[:1] == ["sign"] and len(argv) == 2:
        wheel = Path(argv[1])
        match = re.fullmatch(r"hidee-([\w.]+)-py3-none-any\.whl", wheel.name)
        if not match:
            sys.exit(f"{wheel.name} is not a Hidee wheel.")
        sha = hashlib.sha256(wheel.read_bytes()).hexdigest()
        signature = sign(load(), signed_message(match.group(1), sha)).hex()
        wheel.with_name(wheel.name + ".sig").write_text(signature + "\n")
        print(f"Signed {wheel.name} ({sha[:12]}…)")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
