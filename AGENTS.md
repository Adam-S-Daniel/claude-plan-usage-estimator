## Repo-specific additions

**Run the tests** from the repo root:

```bash
pip install -r requirements-dev.txt && python -m pytest tests -q
```

`requirements-dev.txt` pins the test and fit dependencies exactly; the
`usage_weights` package itself needs none of them.

**A released weight table is never edited.** Each file under
`usage_weights/data/weights/` is named by its table id, and consumers pin an
id, never "latest". A new measurement or a corrected fit is a new dated file.
Build tables, and the README's weights block, only with `kit/build_table.py`
(`--readme README.md` rewrites the block between the `weights:` markers);
`tests/test_table.py` fails when the README and the table file disagree.

**No private data, anywhere.** This repo is public and holds one account's
measurements. `tests/test_data_clean.py` fails on home or mount paths,
machine names, email addresses and similar strings in any file. The files the
fleet's sync writes, and the Dependabot config, are exempt by exact path
(`FLEET_FILES` and `FLEET_DIRS` in that test), so this file is NOT checked:
keep it free of private data by hand. Never widen the exemption to the
package, data, kit or docs.

**Action pins use the legacy form** (full 40-hex SHA, no version comment), not
`actions.lock`: in the lockfile form the sync stops delivering
`agent-checks.yml`, and this repo has two third-party actions.

**Workflows and required checks.** The required contexts on `main` are `test`
and `agent-checks` (declared in repo-settings' `fleet.yml`).

| Workflow | Job | Runs on | Salient-path handling | Required |
| --- | --- | --- | --- | --- |
| `ci.yml` | `test` | `pull_request`, push to `main` | None: a required check cannot use `on.paths`, and every file is tested (tables, function, kit, committed runs, the data-clean scan) | Yes |
| `agent-checks.yml` | `agent-checks` | `pull_request`, push to `main` | None: required; the runner passes when nothing it checks changed. Delivered by the fleet sync | Yes |
| `dependabot-auto-merge.yml` | `sweep` | schedule, `workflow_dispatch` | Not applicable. Delivered by the fleet sync | No |
