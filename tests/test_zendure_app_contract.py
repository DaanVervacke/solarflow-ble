from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

FIXTURE = Path(__file__).parent / "fixtures" / "zendure_app_7_0_0_contract.jsonl"
METADATA = Path(__file__).parent / "fixtures" / "zendure_app_7_0_0_contract.meta.json"

REQUIRED_RECORD_FIELDS = {
    "kind",
    "method",
    "source",
    "evidence",
    "confidence",
    "shape",
    "local_status",
    "local_reference",
    "note",
}
ALLOWED_KINDS = {"lifecycle", "inbound", "outbound"}
ALLOWED_EVIDENCE = {
    "direct_literal",
    "direct_control_flow",
    "direct_literal_and_control_flow",
    "inferred_serializer_shape",
}
ALLOWED_CONFIDENCE = {"high", "medium"}
ALLOWED_LOCAL_STATUS = {"aligned", "diverges", "unknown", "not_applicable"}
PLACEHOLDERS = {
    "<device_id>",
    "<runtime_int>",
    "<runtime_millis>",
    "<property_report_method>",
}
SENSITIVE_KEY_PARTS = (
    "serial",
    "password",
    "token",
    "secret",
    "credential",
    "productkey",
    "devicekey",
)


def _records() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(FIXTURE.read_text().splitlines(), start=1):
        assert line.strip(), f"blank JSONL line at {line_number}"
        record = json.loads(line)
        assert isinstance(record, dict), f"line {line_number} is not an object"
        records.append(record)
    return records


def _walk(value: Any) -> list[tuple[str | None, Any]]:
    found: list[tuple[str | None, Any]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            found.append((key, child))
            found.extend(_walk(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_walk(child))
    return found


def test_contract_records_have_valid_shape_and_enums() -> None:
    records = _records()

    assert records
    for record in records:
        assert record.keys() >= REQUIRED_RECORD_FIELDS
        assert record["kind"] in ALLOWED_KINDS
        assert record["evidence"] in ALLOWED_EVIDENCE
        assert record["confidence"] in ALLOWED_CONFIDENCE
        assert record["local_status"] in ALLOWED_LOCAL_STATUS
        assert isinstance(record["shape"], dict)
        assert isinstance(record["source"], str)
        assert re.search(r"\.java:\d+-\d+", record["source"])


def test_baseline_steps_are_explicit_unique_and_ordered() -> None:
    baseline = [record for record in _records() if record.get("phase") == "baseline"]

    steps = [record.get("step") for record in baseline]
    assert steps == list(range(1, len(baseline) + 1))
    assert len(steps) == len(set(steps))
    assert all(isinstance(step, int) for step in steps)
    assert all(record.get("phase") != "dispatch_only" for record in baseline)
    assert all(
        "step" not in record
        for record in _records()
        if record.get("phase") == "dispatch_only"
    )

    methods = [record["method"] for record in baseline]
    assert methods[:5] == [
        "connect",
        "enable_notifications",
        "BLESPP_OK",
        "getInfo",
        "read",
    ]
    assert methods[5:] == [
        "BLESPP",
        "getInfo-rsp",
        "read_reply",
        "report",
        "error",
        "disconnect",
    ]


def test_outbound_shapes_only_use_documented_keys_and_placeholders() -> None:
    allowed_keys = {
        "BLESPP_OK": {"method", "messageId"},
        "getInfo": {"method", "deviceId", "timestamp", "messageId"},
        "read": {"method", "deviceId", "timestamp", "messageId", "properties"},
    }
    outbound = [record for record in _records() if record["kind"] == "outbound"]

    assert {record["method"] for record in outbound} == set(allowed_keys)
    for record in outbound:
        shape = record["shape"]
        assert set(shape) <= allowed_keys[record["method"]]
        assert shape["method"] == record["method"]
        if record["method"] == "BLESPP_OK":
            assert shape["messageId"] == 1009
        else:
            assert shape["deviceId"] == "<device_id>"
            assert shape["timestamp"] == "<runtime_millis>"
            assert shape["messageId"] == "<runtime_int>"
        if record["method"] == "read":
            assert shape["properties"] == ["getAll"]


def test_placeholders_and_redaction_are_safe() -> None:
    fixture_text = FIXTURE.read_text()
    metadata_text = METADATA.read_text()
    combined_text = fixture_text + metadata_text

    for _, value in _walk(_records()):
        if isinstance(value, str) and value.startswith("<"):
            assert value in PLACEHOLDERS
    assert "DEVICE_SERIAL" not in combined_text
    assert "PACK-" not in combined_text
    assert not re.search(r"-----BEGIN [A-Z ]+-----", combined_text)
    assert not re.search(r"\b(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\b", combined_text)

    for record in _records():
        for key, value in _walk(record):
            if key is not None and any(
                part in key.lower() for part in SENSITIVE_KEY_PARTS
            ):
                raise AssertionError(f"sensitive identity or credential key: {key}")
            if key == "deviceId":
                assert value == "<device_id>"


def test_metadata_identifies_source_artifact_not_runtime_capture() -> None:
    metadata = json.loads(METADATA.read_text())

    assert metadata["package"] == "com.zendure.iot"
    assert metadata["version"] == "7.0.0"
    assert metadata["versionCode"] == 7001
    assert re.fullmatch(r"[0-9a-f]{64}", metadata["base_apk_sha256"])
    assert metadata["decompiler"] == "jadx"
    assert metadata["decompiler_version"] == "1.5.6"
    assert metadata["evidence_mode"] == "source_only"
    assert metadata["runtime_capture"] is False
    assert metadata["unknowns"]
    assert "timing" in " ".join(metadata["unknowns"])
    assert "MTU" in " ".join(metadata["unknowns"])
    assert "fragmentation" in " ".join(metadata["unknowns"])
    assert "keepalive" in " ".join(metadata["unknowns"])
    assert "no real" in metadata["redaction"]
