"""Generate the GIS API bearer-token registry.

The registry stores only SHA-256 hashes; raw tokens are printed ONCE, here, and cannot be
recovered afterwards. Copy them somewhere safe before closing the terminal.

Usage:
    python scripts/generate_api_tokens.py                       # one token per role
    python scripts/generate_api_tokens.py --role GIS_ANALYST --id analyst-alice
    python scripts/generate_api_tokens.py --append              # keep existing tokens
    python scripts/generate_api_tokens.py --show                # list issued tokens (hashes only)

The output file is gitignored. For deployments that inject secrets rather than mounting
files, set GIS_API_TOKENS to the same JSON instead.
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.api.security import hash_token  # noqa: E402
from app.config import auth_config as config  # noqa: E402


def _load(path: Path) -> dict:
    if not path.exists():
        return {"tokens": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _issue(role: str, principal_id: str) -> tuple[str, dict]:
    raw = secrets.token_urlsafe(config.TOKEN_BYTES)
    return raw, {
        "token_hash": hash_token(raw),
        "id": principal_id,
        "name": f"{principal_id} ({role})",
        "roles": [role],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate GIS API bearer tokens.")
    parser.add_argument("--role", choices=sorted(config.ROLE_PERMISSIONS), default=None,
                        help="Issue a single token with this role (default: one per role).")
    parser.add_argument("--id", dest="principal_id", default=None, help="Principal id for the issued token.")
    parser.add_argument("--append", action="store_true", help="Keep existing tokens instead of replacing them.")
    parser.add_argument("--show", action="store_true", help="List issued tokens (ids and roles only) and exit.")
    parser.add_argument("--output", default=None, help=f"Registry path (default: {config.TOKEN_REGISTRY_PATH}).")
    args = parser.parse_args()

    path = Path(args.output) if args.output else config.TOKEN_REGISTRY_PATH

    if args.show:
        registry = _load(path)
        print(f"Registry: {path}")
        if not registry.get("tokens"):
            print("  (empty -- run without --show to issue tokens)")
            return 0
        for entry in registry["tokens"]:
            print(f"  {entry['id']:<24} roles={','.join(entry.get('roles') or [])}  hash={entry['token_hash'][:12]}...")
        return 0

    requests = (
        [(args.role, args.principal_id or args.role.lower().replace("_", "-"))]
        if args.role
        else [(role, role.lower().replace("_", "-")) for role in sorted(config.ROLE_PERMISSIONS)]
    )

    registry = _load(path) if args.append else {"tokens": []}
    existing = {entry["id"] for entry in registry["tokens"]}

    issued: list[tuple[str, str, str]] = []
    for role, principal_id in requests:
        if principal_id in existing:
            print(f"Skipping {principal_id!r}: already in the registry (use a different --id).", file=sys.stderr)
            continue
        raw, entry = _issue(role, principal_id)
        registry["tokens"].append(entry)
        issued.append((principal_id, role, raw))

    if not issued:
        print("Nothing issued.", file=sys.stderr)
        return 1

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")
    # Owner-only: the file holds hashes, but its contents still describe who can do what.
    path.chmod(0o600)

    print(f"Wrote {len(registry['tokens'])} token(s) to {path}\n")
    print("=" * 78)
    print("RAW TOKENS -- shown once, never recoverable. Store them securely now.")
    print("=" * 78)
    for principal_id, role, raw in issued:
        print(f"\n  id      : {principal_id}")
        print(f"  role    : {role}  -> {', '.join(sorted(config.ROLE_PERMISSIONS[role]))}")
        print(f"  token   : {raw}")
        print(f"  usage   : curl -H 'Authorization: Bearer {raw}' ...")
    print("\nRestart the API for the new registry to take effect (it is read once at startup).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
