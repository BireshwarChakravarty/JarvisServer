"""A private certificate so iPhone and iPad can use the microphone with the companion.

Safari only lets a page use the microphone over https. On a home network there's no public
certificate for "Your-Mac.local", so JARVIS makes his own:

  • a small certificate authority ("JARVIS on <Mac>"), created once on this Mac. Its private key
    never leaves the Mac. You install its certificate on each iPhone/iPad as a profile and turn
    on trust for it once.
  • a server certificate for this Mac's network names, signed by that authority and renewed on
    its own (new IP address, or close to expiry). Phones never need to re-trust anything.

Uses the system's openssl (LibreSSL on macOS), so there's nothing extra to bundle.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import ipaddress
import json
import logging
import os
import plistlib
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

log = logging.getLogger("jarvis.tls")

SERVER_DAYS = 397          # Apple's limit for server certificates is 398 days
RENEW_WITHIN_DAYS = 45


def openssl() -> str | None:
    for p in ("/usr/bin/openssl", shutil.which("openssl")):
        if p and os.path.exists(p):
            return p
    return None


def _run(*args: str) -> str:
    r = subprocess.run([openssl() or "openssl", *args], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"openssl {args[0]} failed: {(r.stderr or r.stdout).strip()[:300]}")
    return r.stdout


class Certificates:
    def __init__(self, folder: Path, mac_name: str = "Mac") -> None:
        self.dir = Path(folder)
        self.mac_name = mac_name
        self.ca_key, self.ca_pem = self.dir / "ca.key", self.dir / "ca.pem"
        self.key, self.pem = self.dir / "server.key", self.dir / "server.pem"
        self.meta = self.dir / "server.json"

    # ── the authority ────────────────────────────────────────────────────
    def ensure_ca(self) -> None:
        if self.ca_key.exists() and self.ca_pem.exists():
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.dir, 0o700)
        name = f"JARVIS on {self.mac_name}"[:60].replace("/", "-")
        with tempfile.TemporaryDirectory() as tmp:
            conf = Path(tmp) / "ca.cnf"
            conf.write_text(
                "[req]\ndistinguished_name=dn\nprompt=no\nx509_extensions=v3\n"
                f"[dn]\nCN={name}\nO=JARVIS\n"
                "[v3]\nbasicConstraints=critical,CA:TRUE,pathlen:0\nkeyUsage=critical,keyCertSign,cRLSign\n"
                "subjectKeyIdentifier=hash\n")
            _run("req", "-x509", "-new", "-newkey", "rsa:2048", "-nodes", "-sha256", "-days", "3650",
                 "-keyout", str(self.ca_key), "-out", str(self.ca_pem), "-config", str(conf))
        os.chmod(self.ca_key, 0o600)
        for p in (self.key, self.pem, self.meta):     # anything signed by an older authority is void
            p.unlink(missing_ok=True)
        log.info("created the companion certificate authority")

    # ── the server certificate ───────────────────────────────────────────
    def ensure_server(self, names: list[str], ips: list[str]) -> tuple[Path, Path]:
        """Server cert covering these names/IPs; reissued if they changed or expiry is near."""
        self.ensure_ca()
        names = sorted({n for n in names if n} | {"localhost"})
        ips = sorted({i for i in ips if _is_ip(i)} | {"127.0.0.1"})
        want = {"names": names, "ips": ips, "ca": self.fingerprint()}
        try:
            have = json.loads(self.meta.read_text())
            fresh = (have.get("names") == names and have.get("ips") == ips and have.get("ca") == want["ca"]
                     and dt.datetime.fromisoformat(have["expires"]) - dt.datetime.now()
                     > dt.timedelta(days=RENEW_WITHIN_DAYS)
                     and self.key.exists() and self.pem.exists())
        except Exception:
            fresh = False
        if fresh:
            return self.pem, self.key
        san = ",".join([f"DNS:{n}" for n in names] + [f"IP:{i}" for i in ips])
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            # a certificate's CN holds at most 64 characters; a long Mac name would break it (the names
            # that matter are in subjectAltName, which has no such limit)
            cn = next((n for n in names if len(n) <= 64 and n != "localhost"), "JARVIS companion")
            (t / "req.cnf").write_text(f"[req]\ndistinguished_name=dn\nprompt=no\n[dn]\nCN={cn}\nO=JARVIS\n")
            (t / "ext.cnf").write_text(
                "basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\n"
                f"extendedKeyUsage=serverAuth\nsubjectAltName={san}\n"
                "subjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid,issuer\n")
            _run("req", "-new", "-newkey", "rsa:2048", "-nodes", "-sha256", "-keyout", str(t / "k"),
                 "-out", str(t / "csr"), "-config", str(t / "req.cnf"))
            _run("x509", "-req", "-in", str(t / "csr"), "-CA", str(self.ca_pem), "-CAkey", str(self.ca_key),
                 "-set_serial", str(int.from_bytes(os.urandom(8), "big")), "-days", str(SERVER_DAYS),
                 "-sha256", "-extfile", str(t / "ext.cnf"), "-out", str(t / "pem"))
            shutil.move(str(t / "k"), self.key)
            shutil.move(str(t / "pem"), self.pem)
        os.chmod(self.key, 0o600)
        expires = dt.datetime.now() + dt.timedelta(days=SERVER_DAYS)
        self.meta.write_text(json.dumps({**want, "expires": expires.isoformat(timespec="seconds")}))
        log.info("issued the companion server certificate for %s", ", ".join(names + ips))
        return self.pem, self.key

    # ── what the phone installs ──────────────────────────────────────────
    def ca_der(self) -> bytes:
        self.ensure_ca()
        lines = [ln for ln in self.ca_pem.read_text().splitlines() if ln and not ln.startswith("-----")]
        import base64

        return base64.b64decode("".join(lines))

    def fingerprint(self) -> str:
        return hashlib.sha256(self.ca_der()).hexdigest()

    def mobileconfig(self) -> bytes:
        """An iOS configuration profile carrying the authority's certificate (nothing else)."""
        der = self.ca_der()
        stable = uuid.uuid5(uuid.NAMESPACE_URL, "jarvis-companion-ca:" + hashlib.sha256(der).hexdigest())
        profile = {
            "PayloadType": "Configuration", "PayloadVersion": 1,
            "PayloadIdentifier": f"com.jarvisassistant.companion.{stable}",
            "PayloadUUID": str(uuid.uuid5(stable, "profile")).upper(),
            "PayloadDisplayName": f"JARVIS on {self.mac_name}",
            "PayloadDescription": "Lets this device talk to JARVIS on your Mac securely, so the companion can "
                                  "use the microphone. It only covers your Mac on your home network.",
            "PayloadOrganization": "JARVIS",
            "PayloadContent": [{
                "PayloadType": "com.apple.security.root", "PayloadVersion": 1,
                "PayloadIdentifier": f"com.jarvisassistant.companion.{stable}.ca",
                "PayloadUUID": str(uuid.uuid5(stable, "ca")).upper(),
                "PayloadDisplayName": f"JARVIS on {self.mac_name}",
                "PayloadCertificateFileName": "jarvis-ca.cer",
                "PayloadContent": der,
            }],
        }
        return plistlib.dumps(profile)

    def reset(self) -> None:
        """Forget everything (the phones' installed profiles stop working)."""
        shutil.rmtree(self.dir, ignore_errors=True)


def _is_ip(s: str) -> bool:
    try:
        ipaddress.ip_address(s)
        return True
    except ValueError:
        return False
