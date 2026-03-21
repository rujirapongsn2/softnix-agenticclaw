from nanobot.admin.server import SECURITY_TXT_PATH, resolve_static_asset


def test_security_txt_is_exposed_at_well_known_path() -> None:
    path, content_type = resolve_static_asset("/.well-known/security.txt")
    assert path == SECURITY_TXT_PATH
    assert content_type == "text/plain; charset=utf-8"
