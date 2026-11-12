---
name: closeout
description: Write the closing note for this chat and say whether it is safe to close.
---

Write the closing note for the current chat in this exact format:

# <title>
WINDOW: <folder> on <branch>, <first> to <last>
VERDICT: KEEP | MERGE with <other title> | CLOSE
REASON: <one sentence>
CARRY: <items, or Nothing>
NEXT: <one sentence>
RESUME: cd <cwd> && claude --resume <id>

Use the first rule that matches. Keep the chat if more than 5 percent of the transcript lines failed to parse, or the working directory is missing. Keep it if a file it edited is uncommitted or untracked, or the branch has commits that are not pushed. Keep it if the last thing the person typed has no assistant answer after it. Merge it with another chat in the same folder and branch that was active within 24 hours, and name that chat. Otherwise close it.

CARRY lists uncommitted files, the last request the person made, and any line in the last assistant message that starts with Next, TODO, Left, Remaining, or Still. If none of those exist, write Nothing.

When ~/.claude/skills/closeout/closeout.py exists, run it instead of writing the note by hand:

python3 ~/.claude/skills/closeout/closeout.py note --latest

The chat you are in was written to a moment ago, so it is the newest transcript and --latest picks it. If you know this chat's session id, `--session ID` picks it exactly. The command writes the note to ~/closeouts/YYYY-MM-DD/ and prints its path. Show the note in the chat as well.

When closeout.py is not on the machine, write the note yourself and save it in ~/closeouts/YYYY-MM-DD/, using today's date, as YYYY-MM-DD-<short session id>-<slug>.md.
