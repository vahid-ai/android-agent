from __future__ import annotations

import subprocess
from pathlib import Path

import mac_to_android_corp_cas as installer


def make_cert(tmp_path: Path, common_name: str) -> str:
    key = tmp_path / f"{common_name}.key"
    cert = tmp_path / f"{common_name}.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-days",
            "1",
            "-nodes",
            "-subj",
            f"/CN={common_name}",
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return cert.read_text(encoding="utf-8")


def test_extract_pems_finds_multiple_certificates(tmp_path: Path) -> None:
    first = make_cert(tmp_path, "First Test CA")
    second = make_cert(tmp_path, "Second Test CA")

    pems = installer.extract_pems(f"prefix\n{first}\nnoise\n{second}\nsuffix")

    assert len(pems) == 2
    assert all(pem.startswith("-----BEGIN CERTIFICATE-----") for pem in pems)
    assert all(pem.endswith("-----END CERTIFICATE-----\n") for pem in pems)


def test_explicit_ca_is_selected_without_observed_chain(tmp_path: Path) -> None:
    pem = make_cert(tmp_path, "Explicit Mitmproxy CA")
    meta = installer.cert_meta(pem, source="mitmproxy")

    selected = installer.select_install_candidates([meta], [], explicit_ca_count=1)

    assert selected == [meta]


def test_android_cert_files_use_old_subject_hash(tmp_path: Path) -> None:
    pem = make_cert(tmp_path, "Android Hash CA")
    runner = installer.Runner()
    meta = installer.cert_meta(pem, source="test", runner=runner)

    android_certs = installer.write_android_cert_files([meta], runner=runner, directory=tmp_path)

    expected_hash = installer.android_subject_hash(runner, pem)
    assert android_certs[0].filename == f"{expected_hash}.0"
    assert (tmp_path / f"{expected_hash}.0").read_text(encoding="utf-8") == pem


def test_keychain_is_not_required_when_skip_keychain_and_extra_ca(tmp_path: Path) -> None:
    pem_path = tmp_path / "mitmproxy-ca-cert.pem"
    pem_path.write_text(make_cert(tmp_path, "Cloud Lab CA"), encoding="utf-8")

    rc = installer.main(
        [
            "--skip-keychain",
            "--extra-ca-pem",
            str(pem_path),
            "--dry-run",
            "--stores",
            "legacy",
        ],
    )

    assert rc == 0
