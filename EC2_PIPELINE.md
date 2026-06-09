# Running the Delivery Pipeline on EC2

End-to-end guide for running `deliver.sh` on a headless EC2 instance:
**run eval → push RAW output → convert to harbour CLI format → push CONVERTED bundles.**

The pipeline lives in one script: [`deliver.sh`](./deliver.sh). It orchestrates the
existing `run.sh` (eval) and `scripts/repackage_to_bundle.py` (format conversion),
then commits + pushes to **two** repos:

| Repo | What lands there | When |
|---|---|---|
| [`Ethara-Ai/kensei-datasets`](https://github.com/Ethara-Ai/kensei-datasets) | the **raw** run output (un-converted) | before conversion |
| [`Ethara-Ai/kensei-delievery`](https://github.com/Ethara-Ai/kensei-delievery) | the **converted** harbour CLI bundles | after conversion |

**Both repos use the same dated layout** on `main`:

```
delivery/<YYYY-MM-DD>/<task dir>/
```

All tasks delivered on a given day land in **that day's** date folder. If the date
folder already exists in the repo it is **reused** (not recreated), so 5 tasks run
today all collect under one `delivery/2026-06-09/` folder. Concurrent task
deliveries are **conflict-safe**: each push rebases on top of any push that landed
first and retries (up to 6×), so two tasks finishing together don't clobber.

> Skip the raw push entirely with `--no-raw` (converted-only, old behavior).

---

## 0. What you need before starting

| Requirement | Why | Needed for |
|---|---|---|
| **GitHub PAT** (classic, `repo` scope) | clone private repo + push to datasets & delivery repos | always |
| **git + git-lfs** | push; binaries go through LFS (on by default) | always |
| **python3 + pip** | runs the converter | always |
| **Docker (running)** + agent image | the eval runs in a container | only `--run` |
| **`.env` with API keys** | the agent calls the model | only `--run` |

> The **one** PAT must cover **three** repos in the `Ethara-Ai` org: the private
> `WildClawBench` clone, the `kensei-datasets` (raw) push, and the
> `kensei-delievery` (converted) push. If you use `--no-raw`, datasets isn't needed.

---

## 1. One-time setup on the EC2 box

```bash
# --- system packages (Ubuntu/Debian AMI) ---
sudo apt-get update
sudo apt-get install -y git git-lfs python3 python3-pip
git lfs install

# --- Docker (only needed if you will use --run) ---
sudo apt-get install -y docker.io
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"      # log out/in once so docker works without sudo
```

> Amazon Linux 2023 instead of Ubuntu? Use:
> `sudo dnf install -y git git-lfs python3 python3-pip docker && sudo systemctl enable --now docker`

---

## 2. Set your PAT (every shell session)

```bash
export GITHUB_TOKEN=ghp_your_real_token
```

- Do **not** bake the token into a script or AMI. Set it at run time, or pull it
  from AWS Secrets Manager / SSM Parameter Store.
- `deliver.sh` reads `GITHUB_TOKEN` (or `GH_TOKEN`) and authenticates **both** the
  datasets and delivery pushes non-interactively — no username/password prompt.

---

## 3. Clone this repo (private-repo safe)

```bash
# clone WildClawBench using the PAT (works whether public or private)
git clone "https://x-access-token:${GITHUB_TOKEN}@github.com/Ethara-Ai/WildClawBench.git"
cd WildClawBench

# strip the token back out of origin (hygiene — keeps it out of .git/config)
git remote set-url origin https://github.com/Ethara-Ai/WildClawBench.git

# python deps
pip3 install -r requirements.txt
```

---

## 4. Configure `.env` (only if using `--run`)

```bash
cp .env.example .env
nano .env            # fill in the API keys the agent needs
```

Also make sure the **agent Docker image** `run.sh` expects is available on the box
(loaded from `Images/*.tar` or however you normally provision it). If it's missing,
`run.sh` preflight will fail loudly before any work happens.

---

## 5. Run the pipeline

> Long runs: wrap in `tmux` so an SSH disconnect doesn't kill the job.
> `tmux new -s deliver` … run … detach with `Ctrl-b d`, reattach with `tmux attach -t deliver`.

### A. Full pipeline — run eval, convert, push (the common case)

```bash
export GITHUB_TOKEN=ghp_your_real_token

./deliver.sh --run \
  --task input/ben_cox_8fc24d4b-dd01-44db-95b5-98d0b7786af5 \
  --task input/chris_murray_d0b75eea-81b2-4fbc-8e0d-57c16e39954d
```

For each task this runs the eval in Docker → pushes the **raw** output to
`kensei-datasets` → converts to harbour CLI format → LFS-tracks binaries → pushes
the **converted** bundle to `kensei-delievery`. Both land under
`delivery/<today>/` on `main`.

### B. Test first — everything EXCEPT the push

```bash
./deliver.sh --run --dry-run \
  --task input/ben_cox_8fc24d4b-dd01-44db-95b5-98d0b7786af5 \
  --task input/chris_murray_d0b75eea-81b2-4fbc-8e0d-57c16e39954d
```

### C. Convert + push EXISTING output only (no eval, no Docker, no `.env`)

```bash
export GITHUB_TOKEN=ghp_your_real_token
./deliver.sh                          # all existing output  -> delivery/<today>/
./deliver.sh --persona "ben cox"      # just one existing task
```

---

## 6. Command reference (`deliver.sh`)

| Flag | Meaning |
|---|---|
| `--run` | run the eval first (needs Docker + `.env`); otherwise convert existing output |
| `--task <path>` | a task to run; repeat for several |
| `--all-tasks` | run every task under `input/` |
| `--tasks-file <file>` | run a list of tasks (one path per line) |
| `--persona "<name>"` | convert-only mode: package one existing task by fuzzy name |
| `--model <m>` / `-k <N>` | override run.sh model / number of runs (default: `claude-opus-4.7`, K=1) |
| `--deliverable <dir>` | top folder in **both** repos (default: `delivery`); the dated subfolder is added under it |
| `--date <YYYY-MM-DD>` | override the date subfolder (default: today, local time) |
| `--no-raw` | skip the raw `kensei-datasets` push (converted-only) |
| `--datasets-repo <url>` | override the raw datasets repo URL |
| `--repo <url>` | override the converted delivery repo URL |
| `--branch <name>` | branch for both repos (default: `main`) |
| `--no-lfs` | disable Git LFS (default: LFS on) |
| `--dry-run` | do everything except the final push (commits locally in both clones) |
| `-h`, `--help` | full help |

Run `./deliver.sh --help` for the authoritative list.

---

## 7. Troubleshooting

| Symptom | Fix |
|---|---|
| `clone failed` / hangs | `GITHUB_TOKEN` not set or lacks access. Re-export it; confirm PAT `repo` scope covers all three repos (`WildClawBench`, `kensei-datasets`, `kensei-delievery`). |
| `[raw-dataset] clone failed` | PAT lacks access to `kensei-datasets`. Grant access, or run `--no-raw` to skip it. |
| `push failed` | PAT can read but not write the target repo. Check write access / org SSO authorization on the PAT. |
| `push rejected … rebasing & retrying` | Normal under concurrency — another task pushed first; the script rebases and retries automatically. Only a problem if it exhausts all 6 retries. |
| Raw push says `no changes to commit` unexpectedly | A run's workspace can contain a nested `.git`; the script strips these before committing so files (not a repo-in-repo) are pushed. If you see empty deliveries, confirm the persona's `output/openclaw/<dir>` actually has files. |
| `git-lfs not installed` warning | Install git-lfs (`sudo apt-get install -y git-lfs && git lfs install`) or pass `--no-lfs`. |
| `run.sh` preflight error | Docker not running or agent image missing. Start Docker; load the image. |
| Model/auth errors during `--run` | `.env` keys missing/invalid. |
| SSH dropped mid-run | Use `tmux`/`nohup`; reattach after reconnecting. |

---

## 8. Quick copy-paste (private repo, full pipeline)

```bash
# one-time
sudo apt-get update && sudo apt-get install -y git git-lfs python3 python3-pip docker.io
git lfs install && sudo systemctl enable --now docker && sudo usermod -aG docker "$USER"

# each session
export GITHUB_TOKEN=ghp_your_real_token
git clone "https://x-access-token:${GITHUB_TOKEN}@github.com/Ethara-Ai/WildClawBench.git"
cd WildClawBench
git remote set-url origin https://github.com/Ethara-Ai/WildClawBench.git
pip3 install -r requirements.txt
cp .env.example .env && nano .env        # add API keys

./deliver.sh --run \
  --task input/ben_cox_8fc24d4b-dd01-44db-95b5-98d0b7786af5 \
  --task input/chris_murray_d0b75eea-81b2-4fbc-8e0d-57c16e39954d
```
