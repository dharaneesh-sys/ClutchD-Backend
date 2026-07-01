# AGENTS.md — Session Instructions

## 1. AUTO-LOAD MEMORY AT START

At the **start of every session**, immediately read ALL files from this directory:

```
~/.config/opencode/memory/
```

These files are your persistent memory context:
- `CURRENT_CONTEXT.md` — Active project, task state, open questions
- `LEARNINGS.md` — Technical solutions, gotchas, patterns discovered
- `DECISIONS.md` — Architecture decisions with rationale
- `USER_PREFS.md` — User preferences, workflow habits, coding style
- `CONVERSATION_LOG.md` — Session history, key decisions, files changed

**Loading order:** Read all files. The latest entries in each file take precedence.

## 2. FOLLOW PREFERENCES AUTOMATICALLY

`USER_PREFS.md` contains the user's explicit preferences for how they work.

- **Apply USER_PREFS.md preferences by default** in every session
- Only deviate when the user **explicitly overrides** a preference in the current session ("unless I say otherwise")
- If a preference is incomplete or unclear, follow the spirit of existing preferences

## 3. PERSIST MEMORY AT END OF SESSION

At the **end of every session** (before concluding), append a summary to:

```
~/.config/opencode/memory/CONVERSATION_LOG.md
```

Each entry must include:
- **Date:** ISO timestamp
- **Summary:** What was accomplished this session
- **Key Decisions:** Any architecture or workflow decisions made
- **Files Changed:** List of files created or modified

## 4. SESSION BEHAVIOR

- Treat memory files as **ground truth** for context
- Use `remember` CLI tool to save key info mid-session
- Use `forget` CLI tool to search memory
- The env var `$OPENCODE_MEMORY_DIR` points to the memory directory
