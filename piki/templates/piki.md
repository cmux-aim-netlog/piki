# piki

A pattern for maintaining a team's coding knowledge base with LLMs.

This is a pattern file. It belongs at the root of a piki wiki repo (`org/piki/piki.md`) and is read by every LLM agent that touches the wiki, whether to update it (the maintainer) or to consume it (the team's coding agents). It is also the document you copy-paste to your LLM when first setting piki up — the pattern explains itself, and the rest is for you and your agent to instantiate together.

## The core idea

Most teams' experience with coding agents looks like RAG: you point an agent at a repo, it greps and reads files at query time, and produces an answer. This works, but the agent is rediscovering the codebase from scratch on every session. There's no accumulation. Ask why a module avoids a particular SDK, and the agent has to find scattered hints — or, more often, miss them entirely. The reason lives in a Slack thread, a closed PR, an ADR nobody linked. Nothing is built up. Claude Code, Codex, and every other coding agent works this way by default.

The idea here is different. Instead of letting agents re-derive context from raw code on every session, the team **incrementally builds and maintains a persistent wiki** — a structured, interlinked collection of markdown files that sits beside the code and captures everything the code cannot say on its own. Decisions. Deprecations. Conventions. Cross-repo flows. Gotchas. The wiki is updated automatically when `main` branches change, with an LLM doing the writing and humans doing the reviewing. Coding agents read from it on every non-trivial task.

This is the key difference: **the wiki is a persistent, compounding artifact, and it is consumed by agents.** Provenance is already attached. Stale claims are already flagged. The synthesis already reflects everything the team has decided. The wiki keeps getting richer with every push and every question.

You rarely write the wiki by hand — the maintainer agent writes and updates it, and the team reviews PRs. Humans curate decisions; the LLM does the bookkeeping and synthesis. In practice, you have your code editor on one side and a terminal running a coding agent on the other. The agent silently calls `piki context` before non-trivial work and surfaces relevant decisions before writing code. The wiki is the codebase's memory; the agent is the user; the CLI is the interface.

This pattern applies wherever:

- A team of 5+ uses coding agents daily and feels them re-learning the same things.
- A codebase has tribal knowledge that lives in Slack, in heads, in dead PR comments.
- Multiple repos in an organization depend on each other in non-obvious ways.
- Decisions and deprecations exist that aren't visible from the code alone.
- The cost of an agent doing the wrong thing — silently and confidently — is real.

It does not apply, or applies poorly, when:

- A solo developer can hold the whole codebase in their head.
- A monorepo is small enough that good READMEs and a `CLAUDE.md` suffice.
- The team doesn't use coding agents — the value proposition collapses.

## Architecture

There are four layers.

**Source repositories** — the team's actual code. The repos people commit to every day. These are the source of truth. piki reads from them but never modifies them. The team's workflow does not change.

**The wiki** — a single git repo (typically `org/piki`) containing markdown files. Page types: repo overviews, gotchas, conventions, cross-repo concepts, ADRs, a glossary. The maintainer agent owns this layer's content; humans own the review. Every change is a commit, and every meaningful change is a PR.

**The schema** — `CLAUDE.md` lives at the root of the wiki repo and tells the maintainer agent how the wiki is structured: page types, citation rules, when to create vs update vs supersede, banned behaviors. This is what turns a generic LLM into a disciplined wiki maintainer rather than a creative writer.

**The skill** — `SKILL.md` lives in each source repo (or as a shared skill) and tells *consumer* agents (Claude Code, Codex, cmux) when and how to call `piki` from the terminal. Pre-flight before non-trivial work. Cite the wiki when reasoning. Surface ADR conflicts to the user. Propose new pages when discovering undocumented gotchas.

The schema and the skill are the two halves of the same idea: the maintainer has rules, the consumer has rules, and the wiki is the contract between them.

## Operations

**Sync.** Pushes to a source repo's `main` branch fire a webhook. The piki server diffs the changes, looks up which wiki pages depend on the changed files (via `meta/file-page-index.json`), and runs the maintainer agent against each affected page. Updated pages are committed to the wiki repo, either auto-merged (for high-confidence updates like metadata refresh) or opened as a PR for human review. Sync is incremental — the maintainer never re-derives the whole wiki, and never re-reads files that didn't change. Plumbing (index updates, link integrity, file-page mapping) runs in code, not in the LLM, to avoid burning tokens on bookkeeping.

**Query.** Team members and their coding agents query the wiki through the `piki` CLI. `piki ask` takes a natural-language question and returns an answer with cited sources. `piki context <files>` dumps the wiki pages relevant to a set of files — the canonical pre-flight call for an agent about to work on those files. `piki read <path>`, `piki search`, `piki gotchas <repo>`, `piki adr` cover the rest. Every response includes provenance: file path, line range, commit SHA, or links to other wiki pages. The agent passes the output straight into its context window. The human reads it directly. Same tool, two consumers.

**Lint.** Periodically (CI cron, or on-demand via `piki lint`), the wiki is health-checked. Stale pages — where the cited source ranges have changed but the page hasn't — are flagged. Orphan pages with no inbound links are surfaced. Cross-references that no longer resolve are reported. Pages with claims lacking citations are listed. The lint pass doesn't fix anything automatically; it produces work for the maintainer agent and humans to address. This is what keeps the wiki from rotting as it grows.

**Propose.** Sometimes the maintainer agent doesn't know a page should exist. A consumer agent or a human discovers an undocumented gotcha during real work and runs `piki propose --topic "tenant isolation in billing-service"`. The maintainer drafts a page based on current source code and any existing related pages, and opens a PR. The team reviews and merges. This is the single backdoor for human-curated knowledge to enter the system, and it is intentional.

## The CLI

There is one tool, named `piki`, and it is the only client. Every coding agent reaches the wiki through `bash`. There is no IDE plugin, no proprietary integration, no required protocol — the CLI is the universal contract.

Output principles:

- Everything is markdown on stdout. Agents paste it into context; humans `less` it.
- Every factual response carries provenance. No claim without a source.
- Exit codes carry signal: 0 (found), 1 (no results), 2 (network error), 3 (auth error). Skills can branch on these.
- A `--json` flag is available for tooling that needs structure.
- Calls are stateless. Sessions, caching, and warmup are the server's job.

If you find yourself wanting to add a second client surface (an MCP server, an editor extension, a Slack bot) — write it as a thin wrapper around the CLI rather than as a parallel implementation. The CLI is the canonical surface; everything else is convenience.

## The skill

The CLI is easy. The skill is the leverage.

A coding agent given access to a CLI will use it sometimes — when the agent's prior judgment says "I should look this up." That is not enough. The skill is what turns "sometimes" into "every non-trivial task." It is a short, opinionated policy document that the agent loads at the start of work and follows mechanically:

- Before non-trivial work: run `piki context` on the files you'll touch. Read the gotchas. If your plan contradicts an ADR, surface it before writing code.
- During work: when you encounter a deprecation, an unfamiliar pattern, or a naming dispute, defer to the wiki. The wiki is more current than code comments.
- When citing: use the wiki's own provenance. Make your reasoning auditable.
- After work: if you discovered something the wiki didn't know, propose a page. Don't update the wiki directly.

A good skill is short, imperative, and specific. It tells the agent what to do, not what to think about. The skill is also what makes piki measurable — you can track how often agents call the CLI per task, and tune the skill until that number is high.

## Page types

The wiki has a small, opinionated set of page types. Resist adding more.

- **`repos/<n>/overview.md`** — high-level summary of one repo. What it does, who depends on it, who it depends on. Short.
- **`repos/<n>/gotchas.md`** — the most valuable page type. Things you cannot tell from reading the code: deprecated APIs that still work, weird performance traps, "we tried that and it broke production," historical reasons for ugly decisions. Each item cites a source.
- **`repos/<n>/conventions.md`** — naming, code style, patterns specific to this repo. Used by agents to match team style.
- **`repos/<n>/api.md`** — the public surface this repo exposes to other repos. Auto-derivable from code, but valuable as a stable reference.
- **`concepts/*.md`** — knowledge that crosses repo boundaries. Authentication flow, billing domain, tenant model. Each links to the repo pages it touches.
- **`decisions/YYYY-MM-DD-*.md`** — ADRs. Append-only. Past decisions are never edited, only superseded by new ones. The chronological filename matters: `git log` and `ls` both give you the timeline for free.
- **`glossary.md`** — internal terminology. What does *tenant* mean here? *job*? *workspace*? Disambiguates language for both humans and agents.

That's it. If something doesn't fit one of these, it usually means either (a) it belongs in the source code, or (b) it needs a new concept page that ties existing pages together.

## Citation and provenance

This is where most LLM-maintained knowledge bases fail. The maintainer agent generates fluent prose, the prose looks authoritative, and a year later nobody can tell which claims came from real code and which were hallucinated to fill a gap.

The defense is structural, not aspirational:

- Every page has a `sources` field in its frontmatter, listing file paths, line ranges, and commit SHAs.
- Every factual claim in the body either points to one of those sources, links to another wiki page, or is marked `[NEEDS HUMAN INPUT]`.
- The maintainer is forbidden, by `CLAUDE.md`, from writing claims it cannot cite.
- During sync, if a page's cited line ranges have moved or disappeared, the page is automatically marked `[STALE]` and queued for review.

The payoff is auditability. When a coding agent acts on a wiki claim, that claim has a source. When the source moves, the wiki knows. When a human asks "is this still true?", the answer is one `git blame` away.

## The PR gate

The maintainer agent is not allowed to land arbitrary changes silently. Wiki updates are gated by the kind of change:

- Auto-merge: brand-new pages, frontmatter metadata refresh, simple additions to gotchas with high citation confidence.
- PR with human review: changes to existing factual claims, contradiction resolution, large rewrites, decision supersessions.

The default for ambiguous cases is "open a PR." Trust is built on the maintainer's track record, not assumed. Over time, the auto-merge surface can grow as the team calibrates the maintainer's reliability, but it should never be total.

This is what separates piki from a generative wiki that quietly drifts. The wiki is a git repo precisely so that every change is reviewable, every claim is traceable, and every mistake is revertible.

## Indexing and logging

Two special files hold the wiki together as it grows.

**`index.md`** is content-oriented. It catalogs every page with a one-line summary, organized by type (repos, concepts, decisions, glossary). The maintainer updates it on every meaningful change. Coding agents read it first when answering broad questions, before drilling into specific pages. At the scale of a few hundred pages this is plenty; you don't need an embedding store yet.

**`log.md`** is chronological. It is an append-only record of what happened: each sync, each propose, each lint pass, each manual edit. Entries follow a fixed prefix (`## [2026-04-26] sync | repo-A | 3 pages updated`) so `grep` and `tail` give you a usable timeline.

**`meta/file-page-index.json`** is the only non-markdown file that matters. It is the reverse index: for each tracked source file, which wiki pages cite it. The sync operation reads it; the lint operation maintains it. Without it, sync devolves into total re-derivation, and the token cost destroys the pattern.

## Tips and tricks

- **Seed the wiki by hand for the first week.** Auto-generation produces fluent but shallow pages. A handful of human-written gotchas at the start sets the bar for what a "good" page looks like, and the maintainer mimics the style going forward.
- **Use ADRs as the wiki's spine.** Decisions are the most durable knowledge in any team. Make them the first thing the maintainer drafts when ingesting a new repo, and link everything else back to them.
- **Add the skill to a `CLAUDE.md` or `AGENTS.md` at the source repo root.** Don't assume agents will load standalone `SKILL.md` files; pin the skill where the agent already looks.
- **Track CLI call rate.** Log every `piki context` call. If a repo's agents are using it less than once per task, the skill needs tightening, not the wiki.
- **Prefer Sonnet for writing pages, Flash for impact analysis.** Sync involves two agent kinds: one decides which pages are affected (cheap, frequent), one drafts the updates (expensive, rare). Routing them to different models keeps the cost sane.
- **The maintainer should never delete a page without a PR.** Soft-mark with `[STALE]` or `[SUPERSEDED]` first. Deletion is a human decision.
- **Don't import Slack threads or meeting notes into the wiki.** The temptation is real; the result is noise. Slack belongs in Slack. The wiki is for what's been decided, not what's been said.
- **The wiki is a git repo.** You get version history, branching, blame, and rollback for free. Don't reinvent any of these.

## Why this works

The tedious part of maintaining a knowledge base — the part humans always abandon — is not the writing or the thinking. It's the bookkeeping. Updating cross-references when a function moves. Marking a page stale when its source file changes. Noticing when two pages contradict each other. Linking a new ADR back to the repos it affects. Keeping the index current. Humans give up on this work after a few months because the maintenance cost grows faster than the value. LLMs don't get bored, can touch fifteen files in one pass, and never forget to update a cross-reference.

But the LLM alone is not enough. A wiki maintained only by LLMs drifts: hallucinations harden into "facts," stale prose looks authoritative, and the team eventually stops trusting it. The pattern works because every layer constrains a different failure mode:

- **Code does the bookkeeping** so the LLM doesn't burn tokens on it.
- **The schema constrains what the LLM may write** so it can't invent.
- **Provenance makes every claim traceable** so drift is detectable.
- **The PR gate gives humans the final word** so trust accumulates instead of erodes.
- **The skill makes consumer agents actually read the wiki** so the maintenance pays off.

The wiki succeeds where private team docs fail because the cost of maintenance is near zero, the surface for hallucination is small, and the consumer is right there in the loop, using it on every task.

The pattern is related in spirit to Vannevar Bush's Memex (1945) — a personal, curated knowledge store with associative trails between documents. Bush's vision needed a maintainer he couldn't have. The LLM is that maintainer. piki applies the idea to the one place where the audience is large, the maintenance burden is high, and the consumer is increasingly an agent: a software team's collective understanding of its own code.

## Note

This document is intentionally abstract. It describes the pattern, not a particular implementation. The exact directory structure, the page formats, the CLI's flag ergonomics, the auto-merge thresholds, the choice of model — all of that depends on your team, your repos, and your tolerances. Everything mentioned here is a default to be questioned. For example: your team might want a stricter skill that refuses to write code without a `piki context` call. Your wiki might be small enough that the index file is enough, no search infrastructure required. You might want a richer ADR format with explicit "decision drivers" sections. You might decide your gotchas pages should be the *only* pages and skip overviews entirely.

The right way to use this document is to put it at the root of your piki wiki, hand it to your LLM of choice, and let the two of you argue about which defaults to keep, which to change, and which to extend. The document's only job is to communicate the pattern clearly enough that everything downstream — the schema, the skill, the seeded pages, the lint rules, the auto-merge policy — can follow from it.

Your wiki is your team's memory. Your agent is its scribe. piki is the contract between them.
