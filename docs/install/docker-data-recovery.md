# Preserve an existing Docker installation

Use this before replacing a container created from the older `docker run`
examples. Those examples mounted `/app/omnivoice_data` for the Hugging Face
cache, but application data could remain in `/root/.omnivoice` inside the
container. Removing that container can delete the database, voices, generated
audio, projects, settings and managed engines.

The fresh-install commands now explicitly use `/app/omnivoice_data`. An existing
installation should keep its original in-container paths: database records,
engine environments and symlinks may contain absolute paths. The procedure below
copies the data out to persistent host directories without moving those paths
or overwriting an existing volume.

## Check the existing paths

With the old container still running, inspect its effective data directory:

```bash
docker exec omnivoice python3 -c 'from backend.core.config import DATA_DIR; print(DATA_DIR)'
docker inspect omnivoice --format '{{json .Mounts}}'
```

Replace `omnivoice` with your container name throughout. The following example
is for the former default `/root/.omnivoice`, with the image's default Hugging
Face cache at `/app/omnivoice_data/huggingface`. If the effective data directory
is already `/app/omnivoice_data`, keep its existing volume and environment
setting; there is no legacy-path migration to perform. Back up that volume
before upgrading. For custom data, cache or engine directories, back up every
location shown by your configuration and preserve their mount destinations.

## Back up before replacing anything

Finish active tasks first. Choose a host filesystem with enough space for both
a backup and a working copy of the data and model cache. Run these commands in
the same Bash session, and stop if any command fails:

```bash
backup_dir="$(pwd)/voicestudio-backup-$(date +%Y%m%d-%H%M%S)"
mkdir -m 700 "$backup_dir" &&
docker inspect omnivoice > "$backup_dir/container.json" &&
docker stop omnivoice &&
docker cp omnivoice:/root/.omnivoice "$backup_dir/data" &&
docker cp omnivoice:/app/omnivoice_data "$backup_dir/volume"
```

Copying after the container stops keeps SQLite and its WAL files together and
prevents writes during the backup. Copy whole directories, including hidden
files and engine installations. Inspect both copies before proceeding. Keep
`container.json` private: it includes environment variables and credentials.
Do not remove the old container, delete its volumes or run `docker compose down
-v`.

## Restore with the original paths

Keep the backup untouched and make working copies. Export the **same**
`OMNIVOICE_API_KEY` used by the old installation before starting the replacement.

```bash
cp -a "$backup_dir/data" "$backup_dir/restored-data" &&
cp -a "$backup_dir/volume" "$backup_dir/restored-volume" &&
docker run -d --name omnivoice-migrated \
  -p 127.0.0.1:3900:3900 \
  -e OMNIVOICE_API_KEY="$OMNIVOICE_API_KEY" \
  -e OMNIVOICE_DATA_DIR=/root/.omnivoice \
  --mount "type=bind,src=$backup_dir/restored-data,dst=/root/.omnivoice" \
  --mount "type=bind,src=$backup_dir/restored-volume,dst=/app/omnivoice_data" \
  ghcr.io/debpalash/voicestudio:stable
```

Use the same GPU flags, image architecture/flavor and custom environment
settings as the original container; the command above is the CPU example.
The two bind mounts preserve application paths and the existing Hugging Face
cache. For subsequent upgrades, keep these same mount destinations and data
setting rather than switching to the fresh-install volume example.

Open the app and verify existing voices, projects, generated audio, settings and
installed models. Check `docker logs omnivoice-migrated` for startup errors.
Retain the stopped original container and untouched backup until that succeeds.
If validation fails, stop `omnivoice-migrated` and restart `omnivoice`; the old
container and its volumes have not been removed or overwritten.
