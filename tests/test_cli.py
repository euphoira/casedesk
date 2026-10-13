import json
import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize("command", ["eval", "demo"])
def test_cli_outputs_utf8_even_when_process_starts_with_legacy_encoding(tmp_path, command):
    output = tmp_path / f"{command}.json"
    environment = {
        **os.environ,
        "PYTHONIOENCODING": "cp1252",
        "CASEDESK_MODE": "demo",
        "CASEDESK_DATA_DIR": str(tmp_path / "data"),
    }
    result = subprocess.run(
        [sys.executable, "-m", "casedesk.cli", command, "--output", str(output)],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    console = json.loads(result.stdout.decode("utf-8"))
    artifact = json.loads(output.read_text(encoding="utf-8"))
    assert console == artifact
    assert "退款".encode() in result.stdout
    assert console["mode"] == "demo"
    if command == "eval":
        assert console["passed"] == console["total"] == 9
    else:
        assert console["status"] == "completed"
        assert console["proposal"]["status"] == "pending"


def test_cli_error_diagnostics_are_utf8_with_legacy_stderr(tmp_path):
    environment = {
        **os.environ,
        "PYTHONIOENCODING": "cp1252",
        "CASEDESK_MODE": "live",
        "CASEDESK_API_KEY": "local-test-key",
        "CASEDESK_MODEL": "local-test-model",
        "CASEDESK_BASE_URL": "file:///invalid-endpoint",
        "CASEDESK_DATA_DIR": str(tmp_path / "data"),
    }
    # Endpoint validation fails before the server starts or any network request occurs.
    result = subprocess.run(
        [sys.executable, "-m", "casedesk.cli", "serve"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 1
    diagnostic = result.stderr.decode("utf-8")
    assert "模型服务地址" in diagnostic
    assert "UnicodeEncodeError" not in diagnostic
    assert "local-test-key" not in diagnostic
