import sqlite3
from pathlib import Path

WIKI_DIR = Path.home() / ".wiki"
DB_PATH = WIKI_DIR / ".piki-index.db"


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    fm_text = text[4:end]
    body = text[end + 4:].lstrip("\n")
    meta: dict = {}
    for line in fm_text.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip()
    return meta, body


def _extract_sources(text: str) -> list[str]:
    """Extract file paths from frontmatter sources block."""
    paths = []
    in_sources = False
    for line in text.splitlines():
        if line.strip() == "sources:":
            in_sources = True
            continue
        if in_sources:
            if line.startswith("  - path:"):
                paths.append(line.split("path:")[1].strip())
            elif line and not line.startswith(" "):
                in_sources = False
    return paths


def build_index() -> None:
    if not WIKI_DIR.exists():
        return
    con = sqlite3.connect(DB_PATH)
    con.execute("DROP TABLE IF EXISTS pages")
    con.execute("""
        CREATE VIRTUAL TABLE pages USING fts5(
            path, title, repo, tags, body,
            tokenize='porter ascii'
        )
    """)
    con.execute("DROP TABLE IF EXISTS file_map")
    con.execute("CREATE TABLE file_map (src_path TEXT, wiki_path TEXT)")

    rows = []
    file_map_rows = []

    for md in WIKI_DIR.rglob("*.md"):
        rel = md.relative_to(WIKI_DIR)
        if rel.parts[0] in (".git",):
            continue
        text = md.read_text(errors="ignore")
        meta, body = _parse_frontmatter(text)
        title = body.splitlines()[0].lstrip("# ").strip() if body else str(rel)
        repo = meta.get("repo", "")
        tags = meta.get("tags", "")
        rows.append((str(rel), title, repo, tags, body))

        for src in _extract_sources(text):
            file_map_rows.append((src, str(rel)))

    con.executemany("INSERT INTO pages VALUES (?,?,?,?,?)", rows)
    con.executemany("INSERT INTO file_map VALUES (?,?)", file_map_rows)
    con.commit()
    con.close()


def search(query: str, limit: int = 10) -> list[dict]:
    if not DB_PATH.exists():
        return []
    con = sqlite3.connect(DB_PATH)
    rows = con.execute(
        "SELECT path, title, repo, snippet(pages,4,'[',']','...',20) FROM pages WHERE pages MATCH ? ORDER BY rank LIMIT ?",
        (query, limit),
    ).fetchall()
    con.close()
    return [{"path": r[0], "title": r[1], "repo": r[2], "snippet": r[3]} for r in rows]


def context_for_files(file_paths: list[str]) -> list[dict]:
    if not DB_PATH.exists():
        return []
    con = sqlite3.connect(DB_PATH)
    results = []
    seen = set()
    for fp in file_paths:
        pattern = f"%{Path(fp).name}%"
        rows = con.execute(
            "SELECT DISTINCT wiki_path FROM file_map WHERE src_path LIKE ?",
            (pattern,),
        ).fetchall()
        for (wiki_path,) in rows:
            if wiki_path not in seen:
                seen.add(wiki_path)
                row = con.execute(
                    "SELECT title, repo FROM pages WHERE path = ?", (wiki_path,)
                ).fetchone()
                if row:
                    results.append({"path": wiki_path, "title": row[0], "repo": row[1]})
    con.close()
    return results
