"""Image-resolution evidence must not disclose secrets or invent a cause."""

import hashlib
import json
import logging

import httpx
import pytest

from scripts.discovery.image_resolution_probe import collect_receipt, main, project_response

TENANT = "https://tenant.example.test"
SECRET = "synthetic-private-value"


def test_cardinality_failure_does_not_infer_lookup_scope_or_count():
    result = project_response(
        500, {"code": 13, "message": ("number of maurice_config object is not one " + SECRET)}
    )
    assert result["outcome"] == "maurice_lookup_cardinality_error"
    assert result["lookup_scope"] == "unknown"
    assert result["lookup_count"] == "unknown"
    assert SECRET not in json.dumps(result)


@pytest.mark.parametrize(
    "body",
    [
        {},
        [],
        None,
        {"image_download_url": ""},
        {
            "image_download_url": "https://images.example.test/image?signature=" + SECRET,
        },
    ],
)
def test_success_requires_both_nonempty_https_urls(body):
    assert project_response(200, body)["outcome"] == "incomplete_response"


def test_complete_response_retains_no_urls_or_unknown_fields():
    body = dict.fromkeys(
        ("image_download_url", "image_md5_download_url"),
        "https://images.example.test/file?signature=" + SECRET,
    )
    body["diagnostic"] = SECRET
    result = project_response(200, body)
    assert result["outcome"] == "complete_response"
    assert SECRET not in json.dumps(result)
    assert "images.example.test" not in json.dumps(result)


@pytest.mark.parametrize(
    "url",
    [
        "http://example.test/x",
        "https://user:pw@example.test/x",
        "https://",
        "not-a-url",
        "https://example.test/\nsecret",
    ],
)
def test_invalid_url_is_not_accepted(url):
    assert (
        project_response(200, {"image_download_url": url, "image_md5_download_url": url})["outcome"]
        == "incomplete_response"
    )


@pytest.mark.parametrize("status", [302, 401, 429, 500])
def test_transport_sends_one_documented_request_and_never_retries_or_redirects(status):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(
            status, json={"message": SECRET}, headers={"location": "https://foreign.example.test/"}
        )

    receipt = collect_receipt(
        TENANT,
        TENANT,
        SECRET,
        "baseline",
        "a" * 40,
        "b" * 64,
        transport=httpx.MockTransport(handle),
    )
    assert len(calls) == 1
    assert calls[0].method == "POST"
    assert calls[0].url.path == "/api/register/namespaces/system/get-image-download-url"
    assert json.loads(calls[0].content) == {"provider": "kvm"}
    assert SECRET not in json.dumps(receipt)
    digest = receipt.pop("receipt_sha256")
    assert (
        hashlib.sha256(
            json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        == digest
    )


@pytest.mark.parametrize(
    ("actual", "expected"),
    [
        (TENANT, "https://other.example.test"),
        ("http://tenant.example.test", "http://tenant.example.test"),
        (TENANT + "/path", TENANT + "/path"),
    ],
)
def test_tenant_mismatch_fails_before_network(actual, expected):
    def handle(_request):
        pytest.fail("unexpected request")

    with pytest.raises(ValueError, match="tenant"):
        collect_receipt(
            actual,
            expected,
            SECRET,
            "baseline",
            "a" * 40,
            "b" * 64,
            transport=httpx.MockTransport(handle),
        )


def test_network_exception_is_sanitized_and_not_retried():
    calls = []

    def handle(request):
        calls.append(request)
        raise httpx.ReadTimeout(SECRET, request=request)

    result = collect_receipt(
        TENANT,
        TENANT,
        SECRET,
        "baseline",
        "a" * 40,
        "b" * 64,
        transport=httpx.MockTransport(handle),
    )
    assert len(calls) == 1
    assert result["response"]["outcome"] == "transport_error"
    assert SECRET not in json.dumps(result)


def test_oversized_response_is_bounded_and_sanitized():
    result = collect_receipt(
        TENANT,
        TENANT,
        SECRET,
        "baseline",
        "a" * 40,
        "b" * 64,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"x" * 1048577)),
    )
    assert result["response"]["outcome"] == "response_too_large"


def test_cli_disables_http_logging_before_authenticated_requests(monkeypatch, capsys):
    import scripts.discovery.image_resolution_probe as probe

    def collect(*_args):
        assert not logging.getLogger("httpx").isEnabledFor(logging.INFO)
        return {"response": {"outcome": "complete_response"}}

    monkeypatch.setattr(probe, "collect_receipt", collect)
    monkeypatch.setattr(
        "sys.argv",
        [
            "probe",
            "--expected-url",
            TENANT,
            "--phase",
            "baseline",
            "--source-commit",
            "a" * 40,
            "--spec-sha256",
            "b" * 64,
        ],
    )
    monkeypatch.setattr(logging.root.manager, "disable", logging.NOTSET)
    logging.getLogger("httpx").setLevel(logging.INFO)
    try:
        assert main() == 0
        assert capsys.readouterr().err == ""
    finally:
        logging.disable(logging.NOTSET)
        logging.getLogger("httpx").setLevel(logging.NOTSET)
