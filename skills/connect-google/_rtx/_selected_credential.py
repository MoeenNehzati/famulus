#!/usr/bin/env python3
"""Manage shared Google credential selection."""
from __future__ import annotations
import argparse, json, os, sys, tempfile
from pathlib import Path
from officina.credentials.google import GoogleCredentialError, load_credential_file

def _selected_credential_path(*, home: Path, platform: str) -> Path:
    from officina.common.famulus_paths import resolve_famulus_paths
    return resolve_famulus_paths(platform=platform, home=Path(home), environ=os.environ).config_root / "connect-google" / "selected-credential.json"

def _write_atomically(path: Path, cred_file: Path, account: str, subject: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    p = {"credential_file": str(cred_file.resolve(strict=True)), "account": account, "subject": subject}
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, prefix=".selected-credential.", delete=False, encoding="utf-8") as tmp:
            tmp_path = Path(tmp.name)
            json.dump(p, tmp)
            tmp.write("\n")
            tmp.flush()
            os.fsync(tmp.fileno())
            os.chmod(tmp_path, 0o600)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    os.replace(tmp_path, path)

def select_shared_credential(*, credential_file: str, home: Path | None = None, platform: str | None = None) -> dict:
    home = Path(home) if home else Path.home()
    platform = platform or sys.platform
    try:
        d = load_credential_file(Path(credential_file))
    except GoogleCredentialError as exc:
        return {"error": {"code": "invalid-descriptor", "message": str(exc)}}
    if not d.account: return {"error": {"code": "missing-account", "message": "Credential descriptor must have a non-empty account"}}
    if not d.subject: return {"error": {"code": "missing-subject", "message": "Credential descriptor must have a non-empty subject"}}
    req = {"drive", "calendar", "gmail"}
    miss = req - set(d.granted_services)
    if miss: return {"error": {"code": "missing-services", "message": f"Credential must grant all three; missing: {', '.join(sorted(miss))}"}}
    try:
        _write_atomically(_selected_credential_path(home=home, platform=platform), d.path, d.account, d.subject)
    except Exception as exc:
        return {"error": {"code": "write-failed", "message": str(exc)}}
    return {"credential_file": str(d.path), "account": d.account, "subject": d.subject, "granted_services": sorted(req), "selected": True}

def shared_credential(*, home: Path | None = None, platform: str | None = None) -> dict:
    home = Path(home) if home else Path.home()
    platform = platform or sys.platform
    path = _selected_credential_path(home=home, platform=platform)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"error": {"code": "no-selection", "message": "No credential has been selected"}}
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        return {"error": {"code": "invalid-pointer", "message": f"Selected credential pointer is malformed: {exc}"}}
    if not isinstance(data, dict) or set(data.keys()) != {"credential_file", "account", "subject"}:
        return {"error": {"code": "invalid-pointer", "message": "Selected credential pointer has wrong structure"}}
    cf, acc, subj = data.get("credential_file"), data.get("account"), data.get("subject")
    if not (cf and acc and subj): return {"error": {"code": "invalid-pointer", "message": "Selected credential pointer has empty required fields"}}
    try:
        d = load_credential_file(Path(cf))
    except GoogleCredentialError as exc:
        return {"error": {"code": "descriptor-invalid", "message": f"Credential descriptor is invalid: {exc}"}}
    if str(d.path) != str(Path(cf).resolve(strict=True)): return {"error": {"code": "path-drift", "message": "Credential file path does not match pointer"}}
    if d.account != acc: return {"error": {"code": "account-drift", "message": "Credential account has changed since selection"}}
    if d.subject != subj: return {"error": {"code": "subject-drift", "message": "Credential subject has changed since selection"}}
    req = {"drive", "calendar", "gmail"}
    miss = req - set(d.granted_services)
    if miss: return {"error": {"code": "missing-services", "message": f"Credential no longer grants required services; missing: {', '.join(sorted(miss))}"}}
    return {"credential_file": str(d.path), "account": d.account, "subject": d.subject, "granted_services": sorted(req), "selected": True}

class SelectSharedCredentialInterface:
    def __init__(self, args: list[str]) -> None:
        p = argparse.ArgumentParser()
        p.add_argument("--credential-file", required=True)
        p.add_argument("--home")
        self.args = p.parse_args(args)
    def __call__(self) -> None:
        r = select_shared_credential(credential_file=self.args.credential_file, home=Path(self.args.home) if self.args.home else None)
        sys.exit(1 if "error" in r else 0)
        print(json.dumps(r))

class SharedCredentialInterface:
    def __init__(self, args: list[str]) -> None:
        p = argparse.ArgumentParser()
        p.add_argument("--home")
        self.args = p.parse_args(args)
    def __call__(self) -> None:
        r = shared_credential(home=Path(self.args.home) if self.args.home else None)
        sys.exit(1 if "error" in r else 0)
        print(json.dumps(r))
