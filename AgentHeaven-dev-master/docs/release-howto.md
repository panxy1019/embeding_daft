# AgentHeaven Release How-To<br/>

## 1. Preconditions<br/>
- Both AgentHeaven-dev and AgentHeaven working trees are clean and on `master`.<br/>
- `src/ahvn/version.py` in AgentHeaven-dev is set to the release version (e.g., `0.9.3.dev0` now; update to `0.9.3` when releasing).<br/>
- AgentHeaven-dev GitHub Actions succeed on `master` (`Code Quality Checks`, `Python Tests`). Use the manual `workflow_dispatch` on `Python Tests` if needed.<br/>
- GitHub CLI (`gh`) is logged in and has repo push/PR permissions.<br/>

## 2. Release Steps<br/>
- From AgentHeaven-dev root: run `bash scripts/release.bash` (macOS-safe, no rsync).<br/>
- The script copies AgentHeaven-dev → AgentHeaven (excludes `.git`, `.github`, `.ahvn`, `__assets__`, `__tasks__`, `TODO.md`), creates/forces branch `release-<version>`, pushes, and opens/updates a PR to `master`.<br/>
- The script waits only for AgentHeaven-dev CI (not for public CI) and then finishes.<br/>

## 3. Post-Release<br/>
- On GitHub, review and merge the `release-<version>` PR into `master` (public repo).<br/>
- Create a GitHub Release with tag `v<version>` (or `<version>`); this triggers the `python-publish` workflow.<br/>
- After merge/tag, pull `master` in both repos to stay current.<br/>

## 4. Troubleshooting<br/>
- If the script complains about dirty trees, commit or stash, then retry.<br/>
- If CI is still running on AgentHeaven-dev, rerun via `workflow_dispatch` or wait; the script does not block on public CI.<br/>
- If `gh` auth fails, run `gh auth login` and re-run the script.<br/>
