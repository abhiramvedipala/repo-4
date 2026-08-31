import spanscope


def test_package_imports() -> None:
    # Phase 0 has no behavior to test; this proves the package resolves under uv.
    assert spanscope.__version__ == "0.1.0"
