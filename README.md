# Research runner

```sh
npx skills add random-react-dev/research-runner
```

Ask your agent to use **research** with your question. Only two skill files are installed: `SKILL.md` and `scripts/launch.py`.

The launcher starts a local Codex runner using our model API. Add `--provider claude` to select Claude Code. It uses a temporary server credential, separate CLI configuration and your home directory. It does not contain helper download or installation instructions.

The server reads the PC state and saved manager rules, supplies instructions to the model, and decides whether the helper is required. The runner executes the approved instructions locally. The server verifies helper heartbeats before accepting success. Research progress and the final report appear in the existing dashboard. Reports are untrusted evidence.

First use may install a persistent research helper. Run only on a Mac you authorize the server to manage. CLI execution is unattended with broad shell access; macOS privacy controls still apply.

Requirements: Apple Silicon macOS, working Python 3.10+, and the selected Codex or Claude Code CLI. Provider credentials remain on the server. Private connection state is in `~/Library/Application Support/ResearchRunner`. Existing skill installations are not upgraded or overwritten by this launcher.

```sh
python3 /path/to/skills/research/scripts/launch.py "Your research question"
python3 /path/to/skills/research/scripts/launch.py --status
```

Keep the launcher running until it returns. Repeating an identical question returns its existing result or reports that it is still active. Temporary connection failures retry for up to 60 seconds using the same saved attempt. Finished reports remain saved locally until the server acknowledges them; rerun the same question to retry delivery. An exited agent is not automatically relaunched, and expired, stopped or replaced attempts are not revived. Use a different question for a new task.

The server manager's `helper-policy` command accepts `helper: required` or `helper: skip`, with an optional `device_id`. The default is `required`. Saved manager rules are supplied on each model request. This control is local to the existing server manager.

Server integration code is in `server/`; it requires the existing research-control server. The public skill does not deploy a new server or include provider secrets.
