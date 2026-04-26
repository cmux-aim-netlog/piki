# piki SKILL

> Copy this file to your project repo as `SKILL.md` or merge into `CLAUDE.md`.
> This instructs coding agents (Claude Code, Codex, Cline, Aider, etc.) to
> consult the team wiki before writing code.

---

## Rule: Always fetch context before coding

**Before editing any file**, run:

```bash
piki wiki context <files-you-will-touch>
```

**Before answering architecture or domain questions**, run:

```bash
piki wiki search "<topic>"
```

**Before writing payment, auth, or billing code**, run:

```bash
piki wiki gotchas <repo-name>
```

Do not skip this step. The wiki contains team decisions, deprecated patterns,
and cross-repo dependencies that are NOT in the code.

---

## Trigger patterns

| When you're about to... | Run this first |
|-------------------------|----------------|
| Edit files in `src/handlers/` | `piki wiki context src/handlers/<file>` |
| Add payment / billing logic | `piki wiki gotchas billing-service` |
| Add auth / session logic | `piki wiki gotchas auth-service` |
| Make an architecture decision | `piki wiki adr --topic <subject>` |
| Ask "why does this code do X?" | `piki wiki search "<topic>"` |
| Onboard to a new repo | `piki wiki read repos/<repo-name>/overview` |

---

## Example

User: "Add a refund handler to auth-service"

**Wrong (without piki)**:
```
[writes refund using chargeUser() — V1 SDK that is deprecated]
```

**Correct (with piki)**:
```bash
$ piki wiki context src/handlers/refund.ts
→ repos/auth-service/gotchas.md  ← opens this

$ piki wiki gotchas auth-service
→ "V1 Payment SDK 신규 사용 금지. chargeUser() 금지, payments.v2.charge() 사용"
→ "근거: decisions/2026-04-02-payment-v2-migration.md"

[writes refund using payments.v2.charge() with idempotency key]
```

---

## Setup (first time)

```bash
piki wiki setup    # clone wiki to ~/.wiki/ and build index
```

## Keep in sync

```bash
piki wiki sync     # pull latest + rebuild index
```

---

## All commands

```bash
piki wiki context <files>     # relevant pages for files you'll touch
piki wiki search <query>      # full-text search
piki wiki read <path>         # read a page
piki wiki gotchas <repo>      # known traps for a repo
piki wiki adr --topic <t>     # find architecture decisions
piki wiki sync                # pull latest wiki
```
