#!/usr/bin/env python3
"""
Install corporate TLS-inspection CA certificates into a rooted Android emulator.

The script can run in two modes:

* macOS workstation mode: probe target hosts and search macOS Keychain CAs.
* Cloud lab mode: skip Keychain access and install explicit PEM files, such as
  ~/.mitmproxy/mitmproxy-ca-cert.pem.

For Android 14+ / API 34+ emulator images it updates both the legacy system CA
directory and a temporary bind-mounted Conscrypt APEX CA directory. The APEX
bind mount is intentionally temporary and must be repeated after emulator boot.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import os
import pathlib
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from typing import Iterable, Sequence


PEM_RE = re.compile(
    r"-----BEGIN CERTIFICATE-----\s+.*?\s+-----END CERTIFICATE-----",
    re.DOTALL,
)

LEGACY_CERT_DIR = "/system/etc/security/cacerts"
APEX_CERT_DIR = "/apex/com.android.conscrypt/cacerts"
APEX_WORK_DIR = "/data/local/tmp/android-mitm-cacerts"


@dataclasses.dataclass(frozen=True)
class CertMeta:
    pem: str
    subject: str
    issuer: str
    fingerprint_sha256: str
    source: str


@dataclasses.dataclass(frozen=True)
class AndroidCert:
    meta: CertMeta
    filename: str
    path: pathlib.Path


class CommandError(RuntimeError):
    pass


class Runner:
    def __init__(self, dry_run: bool = False, verbose: bool = False):
        self.dry_run = dry_run
        self.verbose = verbose

    def run(
        self,
        cmd: Sequence[str],
        *,
        input_text: str | None = None,
        check: bool = True,
        timeout: int = 60,
    ) -> subprocess.CompletedProcess[str]:
        if self.verbose or self.dry_run:
            print("[cmd]", " ".join(cmd))
        if self.dry_run and pathlib.Path(cmd[0]).name == "adb":
            return subprocess.CompletedProcess(cmd, 0, "", "")

        proc = subprocess.run(
            list(cmd),
            input=input_text,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
        if check and proc.returncode != 0:
            raise CommandError(
                f"command failed ({proc.returncode}): {' '.join(cmd)}\n{proc.stderr}",
            )
        return proc


def require_tool(name: str) -> None:
    if shutil.which(name) is None:
        raise CommandError(f"required command not found on PATH: {name}")


def extract_pems(text: str) -> list[str]:
    return [match.group(0).strip() + "\n" for match in PEM_RE.finditer(text)]


def normalize_dn(value: str) -> str:
    value = value.strip()
    value = re.sub(r"^(subject|issuer)=\s*", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+", " ", value)
    return value


def openssl_x509(
    runner: Runner,
    pem: str,
    args: Sequence[str],
    *,
    check: bool = True,
) -> str:
    proc = runner.run(
        ["openssl", "x509", "-noout", *args],
        input_text=pem,
        check=check,
    )
    return proc.stdout.strip()


def cert_meta(
    pem: str,
    *,
    source: str,
    runner: Runner | None = None,
) -> CertMeta:
    runner = runner or Runner()
    subject = normalize_dn(openssl_x509(runner, pem, ["-subject"]))
    issuer = normalize_dn(openssl_x509(runner, pem, ["-issuer"]))
    fingerprint = openssl_x509(runner, pem, ["-fingerprint", "-sha256"])
    fingerprint = fingerprint.split("=", 1)[-1].replace(":", "").upper()
    return CertMeta(
        pem=pem,
        subject=subject,
        issuer=issuer,
        fingerprint_sha256=fingerprint,
        source=source,
    )


def android_subject_hash(runner: Runner, pem: str) -> str:
    proc = runner.run(
        ["openssl", "x509", "-subject_hash_old", "-noout"],
        input_text=pem,
        check=False,
    )
    if proc.returncode == 0 and proc.stdout.strip():
        return proc.stdout.strip().splitlines()[0]
    return openssl_x509(runner, pem, ["-subject_hash"]).splitlines()[0]


def dump_keychain_certs(runner: Runner) -> list[CertMeta]:
    if platform.system() != "Darwin":
        raise CommandError(
            "macOS Keychain access requires macOS; use --skip-keychain with "
            "--extra-ca-pem on Linux/cloud hosts.",
        )
    require_tool("security")

    keychains = [
        "/System/Library/Keychains/SystemRootCertificates.keychain",
        "/Library/Keychains/System.keychain",
        str(pathlib.Path.home() / "Library/Keychains/login.keychain-db"),
    ]
    found: dict[str, CertMeta] = {}
    for keychain in keychains:
        if not pathlib.Path(keychain).exists():
            continue
        proc = runner.run(
            ["security", "find-certificate", "-a", "-p", keychain],
            check=False,
            timeout=120,
        )
        for pem in extract_pems(proc.stdout):
            meta = cert_meta(pem, source=keychain, runner=runner)
            found.setdefault(meta.fingerprint_sha256, meta)
    return list(found.values())


def load_extra_ca_pems(paths: Iterable[str], runner: Runner) -> list[CertMeta]:
    certs: list[CertMeta] = []
    for pem_path in paths:
        expanded = pathlib.Path(os.path.expanduser(pem_path))
        text = expanded.read_text(encoding="utf-8")
        pems = extract_pems(text)
        if not pems:
            raise CommandError(f"no PEM certificates found in {expanded}")
        for pem in pems:
            certs.append(cert_meta(pem, source=str(expanded), runner=runner))
    return certs


def probe_host_chain(
    host: str,
    *,
    port: int,
    proxy: str | None,
    runner: Runner,
) -> list[CertMeta]:
    cmd = [
        "openssl",
        "s_client",
        "-showcerts",
        "-servername",
        host,
        "-connect",
        f"{host}:{port}",
    ]
    if proxy:
        cmd += ["-proxy", proxy]
    proc = runner.run(cmd, input_text="", check=False, timeout=45)
    text = proc.stdout + "\n" + proc.stderr
    pems = extract_pems(text)
    if not pems:
        raise CommandError(f"no certificates observed while probing {host}")
    return [
        cert_meta(pem, source=f"probe:{host}", runner=runner)
        for pem in pems
    ]


def select_install_candidates(
    candidate_cas: Sequence[CertMeta],
    observed_chains: Sequence[CertMeta],
    explicit_ca_count: int,
) -> list[CertMeta]:
    explicit = list(candidate_cas[:explicit_ca_count])
    observed_issuers = {cert.issuer for cert in observed_chains}
    selected: dict[str, CertMeta] = {
        cert.fingerprint_sha256: cert
        for cert in explicit
    }
    for cert in candidate_cas[explicit_ca_count:]:
        if cert.subject in observed_issuers:
            selected[cert.fingerprint_sha256] = cert
    return list(selected.values())


def write_android_cert_files(
    certs: Sequence[CertMeta],
    *,
    runner: Runner,
    directory: pathlib.Path,
) -> list[AndroidCert]:
    directory.mkdir(parents=True, exist_ok=True)
    used: dict[str, int] = defaultdict(int)
    android_certs: list[AndroidCert] = []
    for meta in certs:
        digest = android_subject_hash(runner, meta.pem)
        index = used[digest]
        used[digest] += 1
        filename = f"{digest}.{index}"
        path = directory / filename
        path.write_text(meta.pem, encoding="utf-8")
        android_certs.append(AndroidCert(meta=meta, filename=filename, path=path))
    return android_certs


def adb_base(args: argparse.Namespace) -> list[str]:
    cmd = [args.adb]
    if args.serial:
        cmd += ["-s", args.serial]
    return cmd


def adb_shell(runner: Runner, args: argparse.Namespace, command: str) -> str:
    proc = runner.run([*adb_base(args), "shell", command], check=False)
    if proc.returncode != 0:
        raise CommandError(proc.stderr or proc.stdout)
    return proc.stdout.strip()


def adb_shell_ok(runner: Runner, args: argparse.Namespace, command: str) -> bool:
    proc = runner.run([*adb_base(args), "shell", command], check=False)
    return proc.returncode == 0


def prepare_adb_root(runner: Runner, args: argparse.Namespace) -> int:
    runner.run([*adb_base(args), "wait-for-device"], timeout=120)
    runner.run([*adb_base(args), "root"], check=False, timeout=60)
    runner.run([*adb_base(args), "wait-for-device"], timeout=120)
    sdk_raw = adb_shell(runner, args, "getprop ro.build.version.sdk")
    try:
        return int(sdk_raw)
    except ValueError as exc:
        raise CommandError(f"could not read Android SDK version: {sdk_raw}") from exc


def install_legacy_store(
    runner: Runner,
    args: argparse.Namespace,
    android_certs: Sequence[AndroidCert],
) -> None:
    adb_shell_ok(runner, args, "mount -o rw,remount /system")
    adb_shell(runner, args, f"mkdir -p {LEGACY_CERT_DIR}")
    for cert in android_certs:
        remote = f"{LEGACY_CERT_DIR}/{cert.filename}"
        runner.run([*adb_base(args), "push", str(cert.path), remote], timeout=60)
        adb_shell(
            runner,
            args,
            f"chmod 0644 {remote} && chown root:root {remote} "
            f"&& (command -v restorecon >/dev/null && restorecon {remote} || true)",
        )


def install_apex_store(
    runner: Runner,
    args: argparse.Namespace,
    android_certs: Sequence[AndroidCert],
) -> bool:
    if not adb_shell_ok(runner, args, f"test -d {APEX_CERT_DIR}"):
        return False

    adb_shell(
        runner,
        args,
        f"rm -rf {APEX_WORK_DIR} && mkdir -p {APEX_WORK_DIR} "
        f"&& cp {APEX_CERT_DIR}/* {APEX_WORK_DIR}/",
    )
    for cert in android_certs:
        remote = f"{APEX_WORK_DIR}/{cert.filename}"
        runner.run([*adb_base(args), "push", str(cert.path), remote], timeout=60)
    adb_shell(
        runner,
        args,
        f"chmod 0644 {APEX_WORK_DIR}/* && chown root:root {APEX_WORK_DIR}/* "
        f"&& mount --bind {APEX_WORK_DIR} {APEX_CERT_DIR}",
    )
    return True


def install_to_android(
    runner: Runner,
    args: argparse.Namespace,
    android_certs: Sequence[AndroidCert],
) -> None:
    if runner.dry_run:
        sdk = args.android_api
        stores = args.stores
        if stores == "auto":
            stores = "both" if sdk >= 34 else "legacy"
        print(f"[android] Dry run: assuming API {sdk}.")
        print(f"[android] Dry run: would install into {stores} store target.")
        for cert in android_certs:
            print(f"[android] Dry run: would push {cert.filename}.")
        return

    require_tool(args.adb)
    sdk = prepare_adb_root(runner, args)
    print(f"[android] Connected to API {sdk}.")

    stores = args.stores
    if stores == "auto":
        stores = "both" if sdk >= 34 else "legacy"

    if stores in {"legacy", "both"}:
        print(f"[android] Installing into {LEGACY_CERT_DIR}.")
        install_legacy_store(runner, args, android_certs)

    if stores in {"apex", "both"}:
        print(f"[android] Installing into {APEX_CERT_DIR} via bind mount.")
        if not install_apex_store(runner, args, android_certs):
            print(f"[warn] {APEX_CERT_DIR} not present on this image; skipped.")


def parse_host(value: str) -> tuple[str, int]:
    if ":" not in value:
        return value, 443
    host, port = value.rsplit(":", 1)
    return host, int(port)


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Install corporate or mitmproxy CAs into a rooted Android emulator.",
    )
    parser.add_argument("--host", action="append", default=[], help="Target HTTPS host[:port] to probe.")
    parser.add_argument("--proxy", help="HTTP proxy host:port for openssl s_client probing.")
    parser.add_argument("--extra-ca-pem", action="append", default=[], help="Explicit CA PEM file to install.")
    parser.add_argument("--skip-keychain", action="store_true", help="Do not read macOS Keychain certificates.")
    parser.add_argument("--adb", default="adb", help="adb executable path.")
    parser.add_argument("--serial", help="ADB device serial, for example emulator-5554.")
    parser.add_argument(
        "--stores",
        choices=["auto", "legacy", "apex", "both"],
        default="auto",
        help="Android CA store target. auto uses both for API 34+ and legacy otherwise.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print commands without modifying Android.")
    parser.add_argument("--android-api", type=int, default=36, help="API level to assume for --dry-run store selection.")
    parser.add_argument("--verbose", action="store_true", help="Print subprocess commands.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    runner = Runner(dry_run=args.dry_run, verbose=args.verbose)

    try:
        require_tool("openssl")
        extra_certs = load_extra_ca_pems(args.extra_ca_pem, runner)
        keychain_certs: list[CertMeta] = []

        if not args.skip_keychain:
            print("[mac] Dumping certificates from macOS Keychain...")
            keychain_certs = dump_keychain_certs(runner)
            print(f"[mac] Parsed {len(keychain_certs)} unique Keychain certificates.")

        candidate_cas = [*extra_certs, *keychain_certs]
        print(f"[certs] Loaded {len(candidate_cas)} candidate CA certificates.")

        observed: list[CertMeta] = []
        for host_value in args.host:
            host, port = parse_host(host_value)
            print(f"[probe] Probing {host}:{port}...")
            observed.extend(probe_host_chain(host, port=port, proxy=args.proxy, runner=runner))
        if observed:
            print(f"[probe] Observed {len(observed)} certificates across probed hosts.")

        selected = select_install_candidates(
            candidate_cas,
            observed,
            explicit_ca_count=len(extra_certs),
        )
        if not selected:
            raise CommandError("no CA certificates selected for installation")

        print("[certs] Selected certificates:")
        for meta in selected:
            print(f"  - {meta.subject} ({meta.source})")

        with tempfile.TemporaryDirectory(prefix="android-cas-") as tmp:
            android_certs = write_android_cert_files(
                selected,
                runner=runner,
                directory=pathlib.Path(tmp),
            )
            for cert in android_certs:
                print(f"[certs] {cert.meta.subject} -> {cert.filename}")
            install_to_android(runner, args, android_certs)

        print("[done] Restart Chrome or reboot/re-run after emulator restart.")
        return 0
    except (CommandError, OSError, subprocess.SubprocessError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
