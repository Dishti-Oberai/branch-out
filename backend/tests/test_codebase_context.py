from codebase_context import MAX_TOTAL_CHARS, gather_codebase_context


def make_project(tmp_path):
    (tmp_path / "main.py").write_text("print('hello')\n")
    (tmp_path / "utils.py").write_text("def helper():\n    return 1\n")

    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "junk.js").write_text("should not appear")

    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("should not appear either")

    (tmp_path / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\nnotarealpng")

    return tmp_path


def test_includes_real_source_files(tmp_path):
    make_project(tmp_path)
    snapshot = gather_codebase_context(tmp_path)
    assert "--- main.py ---" in snapshot
    assert "print('hello')" in snapshot
    assert "--- utils.py ---" in snapshot
    assert "def helper():" in snapshot


def test_excludes_common_build_and_vcs_directories(tmp_path):
    make_project(tmp_path)
    snapshot = gather_codebase_context(tmp_path)
    assert "--- node_modules/junk.js ---" not in snapshot
    assert "--- .git/config ---" not in snapshot
    assert "should not appear" not in snapshot


def test_excludes_binary_files_by_content_and_extension(tmp_path):
    make_project(tmp_path)
    snapshot = gather_codebase_context(tmp_path)
    assert "logo.png" not in snapshot


def test_respects_gitignore(tmp_path):
    make_project(tmp_path)
    (tmp_path / ".gitignore").write_text("utils.py\nsecrets/\n")
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets" / "key.txt").write_text("do-not-leak")

    snapshot = gather_codebase_context(tmp_path)
    assert "--- main.py ---" in snapshot  # not ignored
    assert "--- utils.py ---" not in snapshot and "def helper():" not in snapshot  # ignored by name
    assert "do-not-leak" not in snapshot  # whole dir ignored


def test_empty_directory_returns_empty_string(tmp_path):
    assert gather_codebase_context(tmp_path) == ""


def test_never_raises_on_unreadable_file(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_bytes(b"\xff\xfe\x00\x01not valid utf8 \x80\x81")
    # Should not raise, and the unreadable file just doesn't show up.
    snapshot = gather_codebase_context(tmp_path)
    assert isinstance(snapshot, str)


def test_oversized_file_is_skipped(tmp_path):
    big = tmp_path / "big.py"
    big.write_text("x = 1\n" * 50_000)  # comfortably over MAX_FILE_BYTES
    small = tmp_path / "small.py"
    small.write_text("y = 2\n")

    snapshot = gather_codebase_context(tmp_path)
    assert "big.py" not in snapshot
    assert "small.py" in snapshot


def test_total_snapshot_size_is_capped(tmp_path):
    for i in range(10):
        (tmp_path / f"file_{i}.py").write_text("z = 1\n" * 10_000)  # ~60KB each, 10 files = ~600KB > cap
    snapshot = gather_codebase_context(tmp_path)
    assert len(snapshot) <= MAX_TOTAL_CHARS + 200  # + a little slack for the truncation marker
    assert "truncated" in snapshot
