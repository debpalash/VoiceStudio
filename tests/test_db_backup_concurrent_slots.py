"""Concurrent migration snapshots must each retain a usable recovery file."""
import builtins
import concurrent.futures
from contextlib import contextmanager
import sqlite3
import threading

import pytest



def test_concurrent_snapshots_cannot_share_a_recovery_slot(tmp_path, monkeypatch):
    from core import db_backup

    path = tmp_path / 'voices.db'
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE voices(name TEXT)')
        connection.execute("INSERT INTO voices VALUES ('keep my voice')")
    counter = db_backup._next_counter
    boundary = threading.Barrier(2)

    def simultaneous_counter(*args):
        value = counter(*args)
        boundary.wait(timeout=5)
        return value

    monkeypatch.setattr(db_backup, '_next_counter', simultaneous_counter)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(db_backup.snapshot_before_migration, str(path), '1.0') for _ in range(2)]
        results = [future.result(timeout=15) for future in futures]
    assert len(set(results)) == 2
    assert set(db_backup.list_backups(str(path))) == set(results)
    for backup in results:
        with sqlite3.connect(backup) as connection:
            assert connection.execute('SELECT name FROM voices').fetchall() == [('keep my voice',)]
    assert not list(tmp_path.glob('*.reserve'))


def test_abandoned_reservation_is_not_reused_or_listed(tmp_path):
    from core import db_backup

    path = tmp_path / 'voices.db'
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE voices(name TEXT)')
    abandoned = tmp_path / 'voices.db.backup-1.0-1.reserve'
    abandoned.touch()
    backup = db_backup.snapshot_before_migration(str(path), '1.0')
    assert backup.endswith('.backup-1.0-2')
    assert db_backup.list_backups(str(path)) == [backup]
    assert abandoned.exists()


def test_failed_copy_releases_its_reservation(tmp_path, monkeypatch):
    from core import db_backup

    import pytest

    path = tmp_path / 'voices.db'
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE voices(name TEXT)')
    connect = sqlite3.connect

    def fail_destination(filename, *args, **kwargs):
        if '.part-' in str(filename):
            raise sqlite3.OperationalError('destination unavailable')
        return connect(filename, *args, **kwargs)

    monkeypatch.setattr(sqlite3, 'connect', fail_destination)
    with pytest.raises(sqlite3.OperationalError, match='destination unavailable'):
        db_backup.snapshot_before_migration(str(path), '1.0')
    assert not list(tmp_path.glob('*.reserve'))
    assert db_backup.list_backups(str(path)) == []


def test_reservations_are_pruned_only_when_the_owner_is_gone(tmp_path, monkeypatch):
    import os
    import time

    from core import db_backup

    path = tmp_path / 'voices.db'
    path.write_bytes(b'')
    dead = tmp_path / 'voices.db.backup-1.0-1.reserve'
    dead.write_text('999999')
    live_old = tmp_path / 'voices.db.backup-1.0-2.reserve'   # owner alive, however old
    live_old.write_text('4242')
    mine = tmp_path / 'voices.db.backup-1.0-3.reserve'
    mine.write_text(str(os.getpid()))
    ownerless_old = tmp_path / 'voices.db.backup-1.0-4.reserve'
    ownerless_old.write_bytes(b'')
    ownerless_fresh = tmp_path / 'voices.db.backup-1.0-5.reserve'
    ownerless_fresh.write_bytes(b'')
    past = time.time() - 48 * 3600
    for p in (dead, live_old, ownerless_old):
        os.utime(p, (past, past))
    monkeypatch.setattr(db_backup, '_pid_alive', lambda pid: pid == 4242)
    db_backup.prune_backups(str(path))
    assert sorted(p.name for p in tmp_path.iterdir() if p.name.endswith('.reserve')) == sorted(
        [live_old.name, mine.name, ownerless_fresh.name]
    )


@pytest.mark.parametrize('fail_copy', [False, True])
def test_snapshot_releases_reservation_during_concurrent_owner_read(tmp_path, monkeypatch, fail_copy):
    from core import db_backup

    database = tmp_path / 'voices.db'
    with sqlite3.connect(database) as connection:
        connection.execute('CREATE TABLE voices(name TEXT)')
        connection.execute("INSERT INTO voices VALUES ('keep my voice')")
    original_open = builtins.open
    original_connect = sqlite3.connect
    original_prune = db_backup.prune_backups
    writer_thread = threading.current_thread()
    reader_open = threading.Event()
    release_reader = threading.Event()
    readers = []

    @contextmanager
    def pause_owner_read(filename, *args, **kwargs):
        with original_open(filename, *args, **kwargs) as stream:
            if str(filename).endswith('.reserve') and threading.current_thread() is not writer_thread:
                reader_open.set()
                assert release_reader.wait(timeout=10)
            yield stream

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        def start_reader():
            readers.append(executor.submit(db_backup.stale_reservations, str(database)))
            assert reader_open.wait(timeout=5)

        def prune_with_reader(*args, **kwargs):
            deleted = original_prune(*args, **kwargs)
            start_reader()
            return deleted

        def connect_with_failure(filename, *args, **kwargs):
            if '.part-' in str(filename):
                start_reader()
                raise sqlite3.OperationalError('destination unavailable')
            return original_connect(filename, *args, **kwargs)

        with monkeypatch.context() as overlap:
            overlap.setattr(builtins, 'open', pause_owner_read)
            if fail_copy:
                overlap.setattr(sqlite3, 'connect', connect_with_failure)
            else:
                overlap.setattr(db_backup, 'prune_backups', prune_with_reader)
            try:
                if fail_copy:
                    with pytest.raises(sqlite3.OperationalError, match='destination unavailable'):
                        db_backup.snapshot_before_migration(str(database), '1.0')
                else:
                    backup = db_backup.snapshot_before_migration(str(database), '1.0')
            finally:
                release_reader.set()
            assert len(readers) == 1
            assert readers[0].result(timeout=5) == []

    assert not list(tmp_path.glob('*.reserve'))
    assert not list(tmp_path.glob('*.part-*'))
    if fail_copy:
        assert db_backup.list_backups(str(database)) == []
    else:
        assert db_backup.list_backups(str(database)) == [backup]
        with sqlite3.connect(backup) as connection:
            assert connection.execute('SELECT name FROM voices').fetchall() == [('keep my voice',)]
    retried = db_backup.snapshot_before_migration(str(database), '1.0')
    assert retried.endswith(f'.backup-1.0-{1 if fail_copy else 2}')
    with sqlite3.connect(retried) as connection:
        assert connection.execute('SELECT name FROM voices').fetchall() == [('keep my voice',)]
    if not fail_copy:
        assert set(db_backup.list_backups(str(database))) == {backup, retried}
