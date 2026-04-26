from pathlib import Path

import typer
from rich.console import Console

console = Console()


def install(
    target_dir: str = typer.Option(".", help="Directory where SKILL.md and llm-wiki.md will be copied."),
    force: bool = typer.Option(False, "--force", help="Overwrite existing files."),
):
    """Install SKILL.md and llm-wiki.md into a project directory."""
    base_dir = Path(__file__).resolve().parents[2]
    src_skill = base_dir / "SKILL.md"
    src_llm = base_dir / "llm-wiki.md"
    dst_dir = Path(target_dir).expanduser().resolve()
    dst_dir.mkdir(parents=True, exist_ok=True)

    targets = [
        (src_skill, dst_dir / "SKILL.md"),
        (src_llm, dst_dir / "llm-wiki.md"),
    ]

    for src, dst in targets:
        if dst.exists() and not force:
            console.print(f"[yellow]Skip[/] {dst} (already exists, use --force to overwrite)")
            continue
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        console.print(f"[green]✓[/] Installed {dst}")

    console.print("\n[bold]Next[/]: run [cyan]piki init[/] then [cyan]piki ingest[/].")
