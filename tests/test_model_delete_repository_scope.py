"""A Git revision identifies history, not a unique model repository."""
from huggingface_hub import scan_cache_dir


def test_deleting_model_does_not_delete_a_fork_with_the_same_revision(tmp_path, monkeypatch):
    from api.routers.setup import download
    import huggingface_hub

    revision = "a" * 40
    for name in ("original", "fork"):
        repo = tmp_path / f"models--test--{name}"
        snapshot = repo / "snapshots" / revision
        snapshot.mkdir(parents=True)
        (repo / "refs").mkdir()
        (repo / "refs" / "main").write_text(revision)
        (snapshot / "config.json").write_text('{"model_type":"test"}')
    info = scan_cache_dir(tmp_path)
    # The Hub API resolves revisions globally. Pick the other repository as
    # the requested deletion so iteration order cannot mask the collision.
    chosen = info.delete_revisions(revision).repos
    requested = next(repo for repo in info.repos if repo.repo_path not in chosen)
    other = next(repo for repo in info.repos if repo is not requested)
    monkeypatch.setattr(huggingface_hub, "scan_cache_dir", lambda: info)
    monkeypatch.setattr(download, "invalidate_cache", lambda: None)
    monkeypatch.setattr(download.hf_progress, "emit", lambda event: None)

    result = download.delete_model(requested.repo_id)

    assert result["deleted"] is True
    assert not requested.repo_path.exists()
    assert other.repo_path.exists()
    assert (other.repo_path / "snapshots" / revision / "config.json").read_text() == '{"model_type":"test"}'


def test_deleting_model_preserves_dataset_with_the_same_id(tmp_path, monkeypatch):
    from api.routers.setup import download
    import huggingface_hub

    for kind, revision in (("models", "a" * 40), ("datasets", "b" * 40)):
        repo = tmp_path / f"{kind}--test--shared"
        snapshot = repo / "snapshots" / revision
        snapshot.mkdir(parents=True)
        (repo / "refs").mkdir()
        (repo / "refs" / "main").write_text(revision)
        (snapshot / "data.json").write_text("{}")
    info = scan_cache_dir(tmp_path)
    monkeypatch.setattr(huggingface_hub, "scan_cache_dir", lambda: info)
    monkeypatch.setattr(download, "invalidate_cache", lambda: None)
    monkeypatch.setattr(download.hf_progress, "emit", lambda event: None)
    download.delete_model("test/shared")
    assert not (tmp_path / "models--test--shared").exists()
    assert (tmp_path / "datasets--test--shared").exists()
