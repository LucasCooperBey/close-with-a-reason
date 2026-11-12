# closeout

closeout.py reads the Claude Code transcripts on this machine, and Codex CLI sessions when those files use the same fields. For each recent chat it writes what the chat was for, which files changed, whether anything is still unsaved, and one next step, then sorts the chats into safe to close, keep open, and merge.

## Quick start

Python 3.9 or newer is enough. There is nothing to install.

Download the one file:

```bash
curl -fsSL -o closeout.py https://raw.githubusercontent.com/LucasCooperBey/close-with-a-reason/main/closeout.py
python3 closeout.py list
python3 closeout.py sweep
```

Or clone the repo and run it from that folder:

```bash
git clone https://github.com/LucasCooperBey/close-with-a-reason.git
cd close-with-a-reason
python3 closeout.py list
python3 closeout.py sweep
```

`list` prints three groups and writes nothing. `sweep` writes one note per chat, plus `INDEX.md`, into `~/closeouts/YYYY-MM-DD` (today's date on this machine). Pass `--out DIR` to write somewhere else.

Only transcripts modified inside the window are opened, so a folder with thousands of old chats stays fast. Claude Code transcripts are read from `~/.claude/projects`. Codex sessions are read from `~/.codex/sessions`. Pass `--projects-dir` and `--codex-dir` if yours live somewhere else. `--days` defaults to 7.

Each note is named `<date>-<short id>-<slug>.md`. The date in the file name is the chat's last activity in UTC. The folder date is the local date when you run the command.

Exit 0 when the command ran, 2 when the arguments are wrong, and 3 when those folders contain no transcripts. The error names the folder it looked in.

## Example

The index and note below are from the sample transcripts in `tests/fixtures/`.

```markdown
# Closeout index

## Safe to close

- [Add a retry](2026-11-12-sessclea-add-a-retry.md): Closed because the last request was answered and the chat edited no files.
- [Rename the flag to days.](2026-11-12-sesstool-rename-the-flag-to-days.md): Closed because the last request was answered and the chat edited no files.
- [Check the codex log](2026-11-12-codexses-check-the-codex-log.md): Closed because the last request was answered and the chat edited no files.
- [Split the header](2026-11-11-sessfar-split-the-header.md): Closed because the last request was answered and the chat edited no files.

## Keep open

- [Save the buffer helper](2026-11-12-sessdirt-save-the-buffer-helper.md): Kept because /tmp/closeout-work/repo/buffer.py is uncommitted.
- [Sketch the index layout and keep every group visible to a stranger who opens the](2026-11-12-sessopen-sketch-the-index-layout-and-keep-every-group-vis.md): Kept because the last request has no assistant answer after it.
- [Broken transcript](2026-11-12-sessgarb-broken-transcript.md): Kept because 1 of 4 lines failed to parse, which is over 5 percent.
- [Not read (rollout-weird.jsonl)](2026-11-12-rolloutw-not-read-rollout-weird-jsonl.md): Not read: rollout-weird.jsonl did not match the transcript shape, so it was not judged.

## Merge

- [Split the sections](2026-11-12-sessmerg-split-the-sections.md): Merge with Split the index because that chat used the same folder and branch and was active within 24 hours.
- [Split the index](2026-11-12-sessmerg-split-the-index.md): Merge with Split the sections because that chat used the same folder and branch and was active within 24 hours.

4 safe to close, 4 keep open, 2 merge.
```

```markdown
# Add a retry
WINDOW: clean-app on main, 2026-11-12 16:00 UTC to 2026-11-12 16:05 UTC
VERDICT: CLOSE
REASON: Closed because the last request was answered and the chat edited no files.
CARRY: Add a retry around the upload.; Next: run the upload once.
NEXT: Run the upload once.
RESUME: cd /tmp/closeout-work && claude --resume sess-clean
```

## How a verdict is decided

The first rule that matches is the one used. A problem keeps the chat open.

1. Keep it open if more than 5 percent of the lines could not be parsed, or if the working directory is not on disk.
2. Keep it open if a file the chat edited is uncommitted or untracked, or if the branch has commits that are not pushed.
3. Keep it open if the last thing typed has no assistant answer after it.
4. Merge it with another chat that used the same folder and branch and was active within 24 hours. The note names that chat.
5. Close it when none of those are true.

Each edited file is checked in the git repo that holds it, including files in a new folder git has not seen yet. A file outside any repo cannot be checked, so it keeps the chat open. A chat whose last assistant step was a tool call, with no reply after it, counts as stopped mid-request.

A file whose records do not match this shape is not read and is not guessed. It stays in the keep group when the file was modified inside the day window. On Windows the git checks are skipped, so a chat that edited files stays in the keep group and the note says why.

`CARRY` lists uncommitted or untracked files, the last request that was typed, and any line in the last assistant message that starts with Next, TODO, Left, Remaining, or Still. If none of those exist, it says Nothing.

## Providers and privacy

closeout.py reads local files and writes local files. Nothing leaves the machine unless a provider is picked, and then only the last 12 turns of that one chat's typed text go to that provider. Tool output is not sent.

The default is `none`, so `list`, `note`, and `sweep` stay on the machine until you pass `--provider`. With `none`, the NEXT line is the last request when it has no answer yet. Once it was answered, NEXT is the line in the last assistant message that starts with Next or TODO, then the uncommitted files, then commits not pushed, and otherwise Nothing left.

`claude` runs `claude -p --no-session-persistence` from a temporary folder, with the instruction and those turns on standard input, so the call does not save a new chat of its own. `ollama` posts to `http://localhost:11434/api/chat` with stream set to false, using the first model that `http://localhost:11434/api/tags` lists. `auto` uses claude when the `claude` command is on PATH, otherwise ollama if it answers within two seconds, otherwise none.

The verdict, the reason, and the carry line are decided on this machine. A provider writes only the NEXT line. If the provider errors, NEXT falls back to the local step and the line says the provider failed.

## Install the skill

From the cloned folder, copy the skill and the script next to each other:

```bash
mkdir -p ~/.claude/skills
cp -r skill/closeout ~/.claude/skills/
cp closeout.py ~/.claude/skills/closeout/
```

In the chat you are about to close, ask for the closeout skill. It runs `python3 ~/.claude/skills/closeout/closeout.py note --latest`, which picks the chat you are in because it is the newest transcript, shows the note, and saves it in `~/closeouts/YYYY-MM-DD/`. Without the script it writes the same note by hand.

## Limits

It reads the transcript format Claude Code writes today, so a format change can make it list a chat as not read. The model-written NEXT line can be wrong, so read it before you close.

Made by Lucas Cooper-Bey as Door 27 of 100 Doors, one free tool a day: https://sandiegoaistudio.com/season
If you like seeing how other people keep track of their AI work, San Diego AI Studio runs Sharathon, where builders show their own setups: https://www.skool.com/sandiegoaistudio
