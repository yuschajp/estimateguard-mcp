"""Tests for the public static-document routes (/privacy-policy.html,
/documentation.html).

Rules under test:
- both routes exist and return the checked-in static files as text/html
- the documentation page links to the privacy policy with a relative href,
  so the two files must be served from the same directory
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import server


def _call(route_fn):
    result = asyncio.run(route_fn(None))
    return result


def test_privacy_policy_route_serves_file():
    resp = _call(server.privacy_policy)
    assert resp.status_code == 200, resp.status_code
    assert resp.media_type == "text/html", resp.media_type
    assert str(resp.path).endswith("static/privacy-policy.html"), resp.path
    assert os.path.isfile(resp.path), resp.path


def test_documentation_route_serves_file():
    resp = _call(server.documentation)
    assert resp.status_code == 200, resp.status_code
    assert resp.media_type == "text/html", resp.media_type
    assert str(resp.path).endswith("static/documentation.html"), resp.path
    assert os.path.isfile(resp.path), resp.path


def test_documentation_links_policy_relatively():
    with open(os.path.join(server.STATIC_DIR, "documentation.html")) as fh:
        html = fh.read()
    assert 'href="privacy-policy.html"' in html, "docs must link policy relatively"


def test_policy_is_self_contained_html():
    with open(os.path.join(server.STATIC_DIR, "privacy-policy.html")) as fh:
        html = fh.read()
    assert "<html" in html and "</html>" in html


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
