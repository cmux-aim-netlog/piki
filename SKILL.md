# piki SKILL

> **Where to put this file**: copy to your source repo as `SKILL.md`, or
> better — paste the rules section into your `CLAUDE.md` / `AGENTS.md`.
> Standalone `SKILL.md` files are not loaded by every agent; pinning into
> `CLAUDE.md` guarantees the agent reads them at the start of every task.
>
> This is the **consumer** skill. It tells coding agents (Claude Code,
> Codex, cmux, Cline, Aider) **when** and **how** to call the `piki` CLI
> on a developer laptop. It is *not* about how the wiki is written —
> that's the maintainer's job and lives in the wiki repo's `CLAUDE.md`.

---

## What counts as "non-trivial work"

Apply the rules below when you're about to do any of these:

- Add a new function, handler, route, command, job, migration, or schema.
- Change behavior of existing business logic (auth, payment, billing,
  permissions, anything domain-specific).
- Add or change an external dependency.
- Touch a module you have not edited in this session before.
- Answer a "why does X work this way?" question about the codebase.

**Skip the rules** for: typo fixes, formatting-only changes, comments,
trivial test additions for code you just wrote.

---

## Rule 1 — Pre-flight (before you write code)

1. List the files you plan to read or modify.
2. Run `piki context <files...>` and read the returned wiki pages **in full**.
3. If the response is empty or low-signal, run `piki search "<topic>"` to
   widen the net.
4. **If your plan contradicts an ADR (anything under `decisions/`), STOP
   and surface the contradiction to the user before writing a single line
   of code.** Never silently violate a decision.

You MUST NOT skip pre-flight on the assumption that you "already know
this codebase." The wiki encodes decisions and conventions that are
*not* in the code.

## Rule 2 — During work (defer to the wiki)

When you encounter any of these, the wiki wins:

- A code comment marking something as deprecated → run
  `piki gotchas <repo>`. The wiki is more current than the comment.
- An unfamiliar pattern that looks like a mistake → run
  `piki search "<pattern>"` before "fixing" it.
- A naming or style dispute → run `piki read repos/<repo>/conventions`.
- A request to add a new dependency → run `piki search "<library>"`.
  The team may have an explicit decision against it.

## Rule 3 — Citation discipline

When the wiki informs your reasoning, **cite it back to the user**. This
makes your decisions auditable.

> Per `repos/<repo>/gotchas.md`: V1 SDK is deprecated; using V2.
> Per ADR `decisions/2026-04-02-…`: rationale is …

Never paraphrase the wiki without the path. Never claim the wiki said
something it did not.

## Rule 4 — Post-flight (after the change)

- If you discovered a new gotcha, convention, or design rationale that
  is **not** in the wiki, mention it to the user and suggest opening a
  wiki page proposal.
- If your changes touched files referenced in any wiki page's `sources:`
  frontmatter, tell the user which pages may now be stale.

## Rule 5 — When the wiki disagrees with the code

If wiki says X and code does Y:

1. Default: trust the wiki. The code may be a stale leftover.
2. Surface the conflict to the user. Do **not** silently pick a side.
3. Once resolved, suggest updating either the code or the wiki to match.

## Rule 6 — When the wiki has nothing to say

If `piki context` and `piki search` both return empty:

1. Proceed cautiously. Treat the area as undocumented territory.
2. After completing the change, mention to the user that this would be
   a good candidate for a new wiki page.

---

## Hard prohibitions

- ❌ **Do not** proceed with code that contradicts an ADR without
     explicit user approval. Surface and ask first.
- ❌ **Do not** cite the wiki for claims it does not actually make.
- ❌ **Do not** edit files in the wiki repo directly. Wiki updates
     happen automatically via `piki ingest-pr` on PR merge, or
     manually via a wiki PR — never as a side effect of consumer work.
- ❌ **Do not** skip the pre-flight on a non-trivial change because the
     CLI "felt slow." Slowness is a wiki signal worth fixing, not a
     reason to bypass.

---

## Exit codes — branch on these

The CLI signals state through exit codes. Use them.

| Code | Meaning | What you should do |
|------|---------|--------------------|
| 0    | found / success | Proceed with the returned context. |
| 1    | no results | Widen with `piki search`, or note "wiki has nothing on this." |
| 2    | network error | Tell the user, retry once, then fall back to local code reading. |
| 3    | auth error | Stop. Tell the user to re-run `piki setup` or refresh credentials. |

---

## Trigger patterns

Replace `<repo>`, `<area>`, `<topic>` with names that exist in your team's wiki.

| When you're about to… | Run this first |
|---|---|
| Edit any file under a domain you don't fully own | `piki context <file...>` |
| Add or modify business logic | `piki gotchas <repo>` |
| Question rationale for an existing pattern | `piki search "<topic>"` |
| Make an architectural decision | `piki adr --topic <topic>` |
| Onboard to a repo you haven't touched | `piki read repos/<repo>/overview` |
| Confirm a naming or style choice | `piki read repos/<repo>/conventions` |

---

## Example (illustrative — your domain will differ)

User: "Add a refund handler to `auth-service`."

**Without piki** (what an unguided agent would do):
```
[finds chargeUser() in src/handlers/, copies the pattern, writes refund]
```
Looks reasonable. Code-only view says it's fine. Ships.
Two days later: production incident — `chargeUser()` was deprecated
months ago, V2 migration is half-done, this PR rolled back team progress.

**With piki** (Rule 1 + Rule 3 in action):
```
$ piki context src/handlers/refund.ts

> repos/auth-service/gotchas.md
>   "V1 Payment SDK 신규 사용 금지. chargeUser() 금지,
>    payments.v2.charge() 사용. 근거: decisions/2026-04-02-payment-v2-migration.md"
> decisions/2026-04-02-payment-v2-migration.md (ADR)
```

Agent's response to the user:
> Per `repos/auth-service/gotchas.md` and ADR `2026-04-02`, V1 payment
> SDK (`chargeUser()`) is deprecated and V2 migration is in progress.
> I see two paths:
>   1. Build the refund through the new V2 adapter (recommended; aligns
>      with the ADR).
>   2. Use V1 temporarily and flag the file for V2 rewrite.
> Which would you like?

Same task. Different outcome. The skill — not the LLM — is what made
the difference.

---

## Setup (run once on a new machine)

```bash
piki setup    # clone wiki to ~/.wiki/ and build the local search index
```

## Keep in sync

```bash
piki sync     # pull latest wiki + rebuild index (run at start of session)
```

---

## CLI cheatsheet

| Need | Command |
|---|---|
| Context for files I'll touch | `piki context <files...>` |
| Search by keyword | `piki search "<query>"` |
| Read a known page | `piki read <path>` |
| All gotchas for a repo | `piki gotchas <repo>` |
| Find / list ADRs | `piki adr [--topic <t>]` |
| Pull latest wiki | `piki sync` |
