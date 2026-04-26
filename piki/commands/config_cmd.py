import typer
from rich.console import Console
from rich.table import Table

import piki.config as cfg

app = typer.Typer(help="Manage piki configuration.")
console = Console()


@app.command("list")
def list_config():
    """List all config values."""
    data = cfg.load()
    if not data:
        console.print("[dim]No config set.[/]")
        return
    table = Table("Key", "Value", show_header=True, header_style="bold cyan")
    for k, v in data.items():
        table.add_row(k, str(v))
    console.print(table)


@app.command("get")
def get_config(key: str):
    """Get a config value."""
    val = cfg.get(key)
    if val is None:
        console.print(f"[yellow]Key '{key}' not found.[/]")
        raise typer.Exit(1)
    console.print(val)


@app.command("set")
def set_config(key: str, value: str):
    """Set a config value."""
    cfg.set_(key, value)
    console.print(f"[green]✓[/] {key} = {value}")


@app.command("delete")
def delete_config(key: str):
    """Delete a config key."""
    if cfg.delete(key):
        console.print(f"[green]✓[/] Deleted '{key}'")
    else:
        console.print(f"[yellow]Key '{key}' not found.[/]")
        raise typer.Exit(1)


@app.command("reset")
def reset_config(
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
):
    """Reset config to defaults."""
    if not yes:
        typer.confirm("Reset all config?", abort=True)
    cfg.reset()
    console.print("[green]✓[/] Config reset to defaults.")
