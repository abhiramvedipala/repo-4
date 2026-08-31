import app


def test_package_imports() -> None:
    # Phase 0 has no endpoints to test; this proves the package resolves under uv.
    assert app.__version__ == "0.1.0"
