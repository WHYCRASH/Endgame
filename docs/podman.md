# Podman notes

This stack is tested with `podman compose` (podman 5.x ships a Compose v2
plugin). `docker compose` also works since the file is Compose-spec compliant.

## Why podman

- Rootless by default - containers run as your user, no daemon, no setuid.
- Compatible with `docker compose` syntax in v2.
- On CachyOS (Arch-based), `pacman -S podman podman-compose` gets you going.

## Setup on CachyOS

```bash
sudo pacman -S podman podman-compose
# Rootful containers need this if you ever want them (this stack doesn't):
sudo systemctl enable --now podman.socket
# For rootless, just use podman directly - no daemon needed.
```

## Build vs. pull

This stack builds three images locally (open-webui, axum-embed, metamcp).
The first build takes a while:
- open-webui: ~5-8 min (npm build + python deps)
- axum-embed: ~3-5 min (cargo release build)
- metamcp: ~3-5 min (pnpm build)

Subsequent `podman compose up` runs are fast - images are cached.

## Rootless gotchas

- Port bindings under 1024 don't work without root. This stack uses ports
  3000, 6333, 6334, 8081, 12008 - all >= 1024, so we're fine.
- Volumes live under `~/.local/share/containers/storage/volumes/` by default.
- File ownership inside bind-mounted volumes can be confusing because the
  container's UID may not match your host UID. The `function-sync` container
  mounts `./functions` and `./tools` read-only - it doesn't write to them, so
  ownership doesn't matter there.
- SELinux labels: if you're on an SELinux-enabled system, you may need to add
  `:Z` to bind mounts so podman relabels them. CachyOS default kernel doesn't
  enforce SELinux, so this is unlikely to bite you. If it does:

      volumes:
        - ./functions:/functions:ro,Z

## podman compose vs podman-compose

- `podman compose` (with a space) - the official Compose v2 plugin, shipped
  with podman 4+. This is what we target. Recommended.
- `podman-compose` (with a hyphen) - the older python wrapper. Works but has
  quirks with `depends_on: condition: service_healthy` and build cache.

If you only have `podman-compose` (the python one), install the v2 plugin:

```bash
# On Arch:
sudo pacman -S podman-compose  # gets you the python one
# For the v2 plugin (preferred):
# It's already bundled with podman on Arch. Run `podman compose version`
# to check.
```

## Common commands

```bash
# Bring everything up
podman compose up -d

# Tail logs (all services)
podman compose logs -f

# Tail one service
podman compose logs -f axum-embed

# Force rebuild a service
podman compose build --no-cache axum-embed
podman compose up -d

# Re-run the one-shot function-sync without restarting the rest
podman compose run --rm function-sync

# Stop everything
podman compose down

# Stop and wipe volumes (NUKE - all data gone)
podman compose down -v
```

## First-boot checklist

1. `git clone --recurse-submodules https://github.com/WHYCRASH/Endgame.git`
2. `cd Endgame && cp .env.example .env`
3. Edit `.env`:
   - `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` - from your providers
   - `WEBUI_SECRET_KEY` - run `openssl rand -hex 32`, paste the output
   - `METAMCP_API_KEY` - any random string
   - `METAMCP_POSTGRES_PASSWORD` - any random string
   - `OPENWEBUI_ADMIN_API_KEY` - leave blank for now, fill in after step 5
4. `podman compose up -d`
5. Wait for open-webui to come up (5-10 min first build). Open
   `http://localhost:3000`, create the admin account.
6. In Open WebUI: Settings > Account > API Keys > generate one. Paste into
   `.env` as `OPENWEBUI_ADMIN_API_KEY`.
7. `podman compose up -d` again. Now function-sync runs successfully and
   installs the `openwebui_function_author` tool.
8. In Open WebUI: Workspace > Tools > enable `openwebui_function_author`.
   Attach it to a model. Test by asking the model to write a function.

## Updating

```bash
# Pull this repo + submodules
git pull --recurse-submodules
# Or update submodules to their latest remote tip:
git submodule update --remote
podman compose up -d --build
```

## Resetting the Open WebUI database

If you ever need to nuke the Open WebUI sqlite db and start over:

```bash
podman compose down
podman volume rm endgame_open-webui-data
podman compose up -d
```

This does NOT touch Qdrant data, MetaMCP's postgres, or the axum-embed model
cache. To wipe everything:

```bash
podman compose down -v
```
