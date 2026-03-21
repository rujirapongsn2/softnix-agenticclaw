from nanobot.admin.server import _public_https_redirect_location


def test_public_https_redirect_location_applies_only_to_public_host() -> None:
    assert _public_https_redirect_location("softnixclaw.softnix.ai", None, "/mobile") == "https://softnixclaw.softnix.ai/mobile"
    assert _public_https_redirect_location("softnixclaw.softnix.ai:80", None, "/admin/auth/me?x=1") == "https://softnixclaw.softnix.ai/admin/auth/me?x=1"
    assert _public_https_redirect_location("127.0.0.1", None, "/") is None
    assert _public_https_redirect_location("10.0.0.2", None, "/") is None
    assert _public_https_redirect_location("softnixclaw.softnix.ai", "proto=https", "/") is None
    assert _public_https_redirect_location("softnixclaw.softnix.ai", "forwarDED=for=1.2.3.4;proto=https;host=example", "/") is None
