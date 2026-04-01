from nanobot.admin.server import PROJECT_ROOT, SECURITY_TXT_PATH, STATIC_DIR, resolve_static_asset


def test_security_txt_is_exposed_at_well_known_path() -> None:
    path, content_type = resolve_static_asset("/.well-known/security.txt")
    assert path == SECURITY_TXT_PATH
    assert content_type == "text/plain; charset=utf-8"


def test_favicon_matches_mobile_app_icon() -> None:
    path, content_type = resolve_static_asset("/favicon.ico")
    assert path == STATIC_DIR / "mobile" / "apple-touch-icon.png"
    assert content_type == "image/png"


def test_docs_images_route_serves_nested_connector_icons() -> None:
    path, content_type = resolve_static_asset("/docs/images/Connectors/icons8-github-logo-48.png")
    assert path == PROJECT_ROOT / "docs" / "images" / "Connectors" / "icons8-github-logo-48.png"
    assert content_type == "image/png"


def test_docs_images_route_serves_composio_logo() -> None:
    svg_path, svg_content_type = resolve_static_asset("/docs/images/Connectors/composio.svg")
    assert svg_path == PROJECT_ROOT / "docs" / "images" / "Connectors" / "composio.svg"
    assert svg_content_type == "image/svg+xml"

    path, content_type = resolve_static_asset("/docs/images/Connectors/composio.png")
    assert path == PROJECT_ROOT / "docs" / "images" / "Connectors" / "composio.png"
    assert content_type == "image/png"


def test_docs_images_route_handles_spaced_filenames() -> None:
    path, content_type = resolve_static_asset("/docs/images/Logo%20Softnix.png")
    assert path == PROJECT_ROOT / "docs" / "images" / "Logo Softnix.png"
    assert content_type == "image/png"
