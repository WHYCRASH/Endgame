# Build notes

CI workflow was not added because the deployment token could not write to
`.github/workflows/`. If you want fmt + clippy + check on push, either:
- Re-run with an elevated token, or
- Add the workflow file by hand from the example below.

## Suggested `.github/workflows/axum-embed.yml`

```yaml
name: axum-embed CI
on:
  push:
    branches: [main]
    paths: ['axum-embed/**', '.github/workflows/axum-embed.yml']
  pull_request:
    paths: ['axum-embed/**']
jobs:
  check:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: axum-embed
    steps:
      - uses: actions/checkout@v4
        with:
          submodules: recursive
      - uses: dtolnay/rust-toolchain@stable
      - uses: Swatinem/rust-cache@v2
        with:
          workspaces: axum-embed
      - run: cargo fmt --all -- --check
      - run: cargo clippy --all-targets -- -D warnings
      - run: cargo check --all-targets
```

## Suggested `.github/workflows/sync.yml`

```yaml
name: sync CI
on:
  push:
    branches: [main]
    paths: ['sync/**', '.github/workflows/sync.yml']
  pull_request:
    paths: ['sync/**']
jobs:
  check:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: sync
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install -r requirements.txt
      - run: python -m py_compile sync_functions.py
      # Optionally: ruff or black --check
```
