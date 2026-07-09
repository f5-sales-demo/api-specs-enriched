"""Prose terminology normalization in upstream F5 descriptions.

The Lint Code Base gate (textlint terminology) flags genuine prose typos in
upstream F5 text — e.g. "Java Script" (should be "JavaScript") and "webpages"
(should be "web pages"). These are normalized here at the enrichment source so
the generated provider docs are lint-clean.

Critically, API identifiers that merely *look* like terminology errors —
the `cloudfront` connector enum, the `salesforce_commerce_connector` enum, and
the literal `/common.js` path — MUST be preserved byte-for-byte; rewriting them
would corrupt the API contract.
"""

from __future__ import annotations

from scripts.utils.branding import BrandingTransformer


def _t() -> BrandingTransformer:
    return BrandingTransformer()


def test_java_script_normalized_to_javascript() -> None:
    out = _t().transform_text("Web client will fetch F5 Client Java Script from this path.")
    assert "JavaScript" in out
    assert "Java Script" not in out


def test_webpages_normalized_to_web_pages() -> None:
    out = _t().transform_text("For Client-Side Defense to work on the webpages where you injected the JS.")
    assert "web pages" in out
    assert "webpages" not in out


def test_common_js_path_preserved() -> None:
    # A literal path value — must not be rewritten to "CommonJS" or anything else.
    text = "If not specified, default to '/common.js'. Example: `\"/common.js\"`."
    assert _t().transform_text(text) == text


def test_connector_enum_values_preserved() -> None:
    # API enum values / schema identifiers — must survive untouched.
    text = 'Enum: ["cloudflare","cloudfront","custom_connector","f5_big_ip","salesforce_commerce_connector"]'
    assert _t().transform_text(text) == text
