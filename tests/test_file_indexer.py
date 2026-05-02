import os

import file_indexer as fi


def test_machine_scoped_latest_and_duplicates_do_not_mix(tmp_path):
    db = tmp_path / "test.sqlite"
    conn = fi.open_db(str(db))
    fi.init_schema(conn, "ALPHA")
    scan_a = fi.start_scan(conn, "ALPHA", "C:\\", "C:\\")
    scan_b = fi.start_scan(conn, "BETA", "C:\\", "C:\\")
    conn.executemany(
        """
        INSERT INTO files(scan_id, machine_name, volume, dirpath, name, size, ctime_ns, atime_ns, mtime_ns, sha256)
        VALUES (?, ?, 'C:\\', ?, ?, ?, 0, 0, 0, ?)
        """,
        [
            (scan_a, "ALPHA", "C:\\a", "same.bin", 10, "aaa"),
            (scan_a, "ALPHA", "C:\\b", "same.bin", 10, "aaa"),
            (scan_b, "BETA", "C:\\x", "same.bin", 10, "bbb"),
        ],
    )
    conn.commit()

    assert fi.latest_scan_id(conn, "ALPHA", "C:\\") == scan_a
    rows = fi.duplicate_groups(conn, scan_a, "ALPHA", "size_name", 10)
    assert len(rows) == 1
    assert rows[0]["n"] == 2
    assert fi.duplicate_groups(conn, scan_b, "BETA", "size_name", 10) == []


def test_reset_latest_is_machine_and_volume_scoped(tmp_path):
    db = tmp_path / "test.sqlite"
    conn = fi.open_db(str(db))
    fi.init_schema(conn, "ALPHA")
    old_scan = fi.start_scan(conn, "ALPHA", "C:\\", "C:\\")
    latest_scan = fi.start_scan(conn, "ALPHA", "D:\\", "D:\\")
    other_machine = fi.start_scan(conn, "BETA", "D:\\", "D:\\")

    deleted = fi.reset_scans(conn, "ALPHA", "D:\\", "latest")

    assert deleted == 1
    remaining = [row["id"] for row in conn.execute("SELECT id FROM scans ORDER BY id")]
    assert remaining == [old_scan, other_machine]
    assert latest_scan not in remaining


def test_config_excludes_windows_paths():
    cfg = fi.AppConfig(
        exclude_dirnames=["Windows", "Program Files"],
        exclude_globs=[],
        only_user="georg",
        follow_hidden_dirs=False,
    )
    assert fi.is_excluded_dir(os.path.join("C:\\", "Windows"), "C:\\", cfg)
    assert fi.is_excluded_dir(os.path.join("C:\\", "Program Files"), "C:\\", cfg)
    assert fi.is_excluded_dir(os.path.join("C:\\", "Users", "other"), "C:\\", cfg)
