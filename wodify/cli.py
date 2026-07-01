"""Command-line interface for the Wodify Hermes tool.

Implemented with ``typer``. Exposes ``discover``, ``login``, ``get-classes``,
and ``book`` on top of :class:`wodify.client.WodifyClient`.
"""

from __future__ import annotations

import json
import sys
from typing import Optional

import typer

from .client import WodifyClient, discover_config
from .config import load_config, save_config

app = typer.Typer(add_completion=False, help="Wodify Hermes CLI")

def get_client() -> WodifyClient:
    config = load_config().model_dump(exclude_none=True)
    return WodifyClient(config)


def _persist_if_hashes_changed(client: WodifyClient) -> None:
    """Quietly persist refreshed version hashes when they changed (bookkeeping)."""
    if client.version_changed:
        save_config(client.config_updates())


@app.command()
def discover(
    gym_subdomain: str = typer.Option(..., "--gym-subdomain", prompt="Gym subdomain"),
    email: str = typer.Option(..., "--email", prompt=True),
    password: str = typer.Option(..., "--password", prompt=True, hide_input=True),
) -> None:
    """Discover and save Wodify configuration values."""

    load_config()
    updates = discover_config(
        gym_subdomain=gym_subdomain,
        email=email,
        password=password,
    )
    config = save_config(updates)

    typer.echo("Discovery complete. Saved configuration:")
    typer.echo(f"Gym subdomain: {updates['gym_subdomain']}")
    typer.echo(f"Base URL: {config.base_url}")
    typer.echo(f"Email: {config.email}")
    typer.echo(f"Membership ID: {updates['membership_id']}")
    typer.echo(f"Version hashes: {updates['version_hashes']}")


@app.command()
def login(
    gym_subdomain: str | None = typer.Option(
        None,
        "--gym-subdomain",
        envvar="WODIFY_GYM_SUBDOMAIN",
        help="Wodify gym subdomain. Defaults to saved config, then delraybeach.",
    ),
    email: str = typer.Option(..., "--email", prompt=True, envvar="WODIFY_EMAIL"),
    password: str = typer.Option(
        ...,
        "--password",
        prompt=True,
        hide_input=True,
        envvar="WODIFY_PASSWORD",
    ),
) -> None:
    """Log in to Wodify and persist discovered session configuration."""
    client = get_client()
    try:
        result = client.login(email, password, gym_subdomain=gym_subdomain)
    except Exception as exc:
        typer.echo(f"Login failed: {exc}", err=True)
        sys.exit(1)

    save_config(client.config_updates())
    label = f" as {result.first_name}" if result.first_name else ""
    typer.echo(f"Login successful{label}.")

@app.command(name="get-classes")
def get_classes(date: Optional[str] = typer.Option(None, "--date"),
                program_filter: Optional[str] = typer.Option(
                    None, "--program-filter",
                    help="Comma-separated numeric program IDs (not names). Defaults to all known programs."),
                json_output: bool = typer.Option(
                    False, "--json", help="Emit the schedule as JSON (for scripting/agents).")) -> None:
    """Fetch and display available classes."""
    client = get_client()
    try:
        classes = client.get_classes(date=date, program_filter=program_filter)
    except Exception as exc:
        typer.echo(f"Error fetching classes: {exc}{client.drift_note()}", err=True)
        raise SystemExit(1)
    _persist_if_hashes_changed(client)
    if json_output:
        typer.echo(json.dumps([cls.model_dump() for cls in classes], indent=2))
        return
    if not classes:
        typer.echo("No classes found.")
        return
    for cls in classes:
        if cls.is_cancelled:
            status = "CANCELLED"
        elif not cls.bookable:
            status = "FULL"
        else:
            status = f"{cls.available} open"
        clock = cls.start_time[:5] if cls.start_time else cls.start
        typer.echo(f"{clock}  {cls.name}  [{status}]  id={cls.id}")

@app.command()
def book(class_id: int = typer.Argument(..., help="ID of the class to book"),
         program_id: Optional[int] = typer.Option(None, "--program-id"),
         dry_run: bool = typer.Option(
             False, "--dry-run", help="Resolve everything but do NOT send the booking request."),
         json_output: bool = typer.Option(
             False, "--json", help="Emit the booking result as JSON.")) -> None:
    """Book a specific class."""
    client = get_client()
    try:
        resp = client.book_class(class_id, program_id, dry_run=dry_run)
    except Exception as exc:
        typer.echo(f"Booking failed: {exc}{client.drift_note()}", err=True)
        raise SystemExit(1)
    _persist_if_hashes_changed(client)
    if json_output:
        typer.echo(json.dumps(resp))
        if not resp.get("success"):
            sys.exit(1)
        return
    if resp.get("success"):
        typer.echo(resp.get("message", "Booked successfully."))
    else:
        typer.echo(resp.get("message", "Booking failed."), err=True)
        sys.exit(1)

if __name__ == "__main__":
    app()
