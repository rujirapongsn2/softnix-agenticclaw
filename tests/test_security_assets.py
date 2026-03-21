from nanobot.admin.server import SECURITY_TXT_PATH, STATIC_DIR, resolve_static_asset


def test_security_txt_is_exposed_at_well_known_path() -> None:
    path, content_type = resolve_static_asset("/.well-known/security.txt")
    assert path == SECURITY_TXT_PATH
    assert content_type == "text/plain; charset=utf-8"


def test_favicon_matches_mobile_app_icon() -> None:
    path, content_type = resolve_static_asset("/favicon.ico")
    assert path == STATIC_DIR / "mobile" / "apple-touch-icon.png"
    assert content_type == "image/png"
