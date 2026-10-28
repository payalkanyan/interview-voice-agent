def test_coach_package_is_importable() -> None:
    """The src layout is installed correctly and `coach` resolves."""
    import coach

    assert coach.__name__ == "coach"
