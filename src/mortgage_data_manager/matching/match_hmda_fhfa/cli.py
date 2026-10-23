"""CLI for HMDA-FHFA matching workflow.

This module provides command-line access to the HMDA-FHFA matching pipeline
for linking HMDA loan applications to FHFA GSE (Fannie Mae/Freddie Mac) data.
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from mortgage_data_manager.core.cli import OverwriteOption
from mortgage_data_manager.core.logging import configure_logging
from mortgage_data_manager.matching.match_hmda_fhfa.config import HMDAFHFAMatchingConfig
from mortgage_data_manager.matching.match_hmda_fhfa.matching_post2018 import MAX_YEAR, MIN_YEAR

app = typer.Typer(
    name="hmda-fhfa",
    help="HMDA-FHFA matching workflow (HMDA ↔ FHFA)",
    no_args_is_help=True,
)

console = Console()

ERAS = ("post2018", "pre2018")

_ERA_HELP = "Matching era: 'post2018' (default) or 'pre2018' (2008-2017)"


@app.callback()
def callback():
    """[bold cyan]HMDA-FHFA Matching Workflow[/bold cyan].

    Tools for matching HMDA loan applications with FHFA GSE loan data from Fannie Mae
    and Freddie Mac.
    """
    configure_logging(level="INFO")


@app.command()
def info() -> None:
    """Show paths and every round of both eras, read from the round definitions."""
    from mortgage_data_manager.matching.match_hmda_fhfa.rounds import (
        ROUNDS_POST2018,
        ROUNDS_PRE2018,
    )

    paths = Table(title="Paths", show_header=False)
    paths.add_column("Setting", style="cyan")
    paths.add_column("Value")
    for label, path in (
        ("Matching dir", HMDAFHFAMatchingConfig.HMDA_FHFA_MATCHING_DIR),
        ("Crosswalk dir", HMDAFHFAMatchingConfig.HMDA_FHFA_CROSSWALK_DIR),
        ("Post-2018 crosswalk", HMDAFHFAMatchingConfig.get_crosswalk_path("post2018")),
        ("Pre-2018 crosswalk", HMDAFHFAMatchingConfig.get_crosswalk_path("pre2018")),
    ):
        mark = "[green]✓[/green]" if path.exists() else "[yellow]missing[/yellow]"
        paths.add_row(label, f"{path}  {mark}")
    console.print(paths)

    post = Table(title=f"Post-2018 rounds ({MIN_YEAR}-{MAX_YEAR})", header_style="bold magenta")
    for col in ("#", "Pool", "Years", "Join adds", "Rate / term", "Checks", "After 1:1"):
        post.add_column(col)
    for number, r in enumerate(ROUNDS_POST2018, start=1):
        joins = [k for k, on in (("purchaser", r.purchaser_key), ("rate", r.rate_key)) if on]
        checks = [
            k
            for k, on in (
                ("same-year orig", r.current_year_orig),
                (f"purpose {r.purpose}", r.purpose != "off"),
                ("GSE cross-year needs prior-year orig", r.prior_year_for_gse),
                ("pt 0 only cross-year", r.cross_year_for_pt0),
                ("demographics", r.demographics),
                ("DTI" + (" (skip if unique)" if r.dti_skip_when_unique else ""), True),
            )
            if on
        ]
        post.add_row(
            str(number),
            ", ".join(r.pool),
            "FHFA >= HMDA" if r.cross_year else "same",
            ", ".join(joins) + (", 2010→2020 bridge" if r.vintage_bridge else "") or "-",
            f"±{r.rate_tol} / ±{r.term_tol}",
            ", ".join(checks),
            f"±{r.post_unique[0]} / ±{r.post_unique[1]}" if r.post_unique else "-",
        )
    console.print(post)

    pre = Table(title="Pre-2018 rounds (2008-2017)", header_style="bold magenta")
    pre.add_column("#")
    pre.add_column("Round")
    for number, pr in enumerate(ROUNDS_PRE2018, start=1):
        pre.add_row(str(number), pr.description)
    console.print(pre)
    console.print(
        "Run: mortgage-data match hmda-fhfa run [--era pre2018]. "
        "Change or add a round: edit ROUNDS_POST2018 or ROUNDS_PRE2018 in rounds.py.\n"
    )


@app.command()
def run(
    min_year: int = typer.Option(
        MIN_YEAR,
        "--min-year",
        "-m",
        help="Start year (inclusive)",
    ),
    max_year: int = typer.Option(
        MAX_YEAR,
        "--max-year",
        "-M",
        help="End year (inclusive)",
    ),
    era: str = typer.Option(
        "post2018",
        "--era",
        help=_ERA_HELP,
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Show detailed progress output",
    ),
    save: bool = typer.Option(
        True,
        "--save/--no-save",
        help="Save crosswalk outputs to disk",
    ),
    overwrite: OverwriteOption = False,
) -> None:
    """Run the HMDA-FHFA matching workflow, for one era.

    --era post2018 (default) runs the rounds in `ROUNDS_POST2018` (rounds.py; see `info`). It
    caches nothing and always rebuilds, so `--overwrite` has no effect there.

    --era pre2018 (2008-2017) runs the three-round workflow: same-year exact
    income, a 1-year-lag round on the residual, then a demographic tie-break of
    the cells Round 1 left ambiguous. Its `--min-year` / `--max-year` default
    to 2008-2017 rather than the post-2018 range, and
    `--overwrite` rebuilds its cached cleaned intermediates (required after an
    upstream HMDA or FHFA silver rebuild).

    Examples:
      mortgage-data match hmda-fhfa run
      mortgage-data match hmda-fhfa run --min-year 2020 --max-year 2023
      mortgage-data match hmda-fhfa run --era pre2018
      mortgage-data match hmda-fhfa run --era pre2018 --overwrite
      mortgage-data match hmda-fhfa run --verbose
      mortgage-data match hmda-fhfa run --no-save
    """
    from mortgage_data_manager.matching.match_hmda_fhfa import (
        run_matching,
        run_matching_pre2018,
    )

    if era not in ERAS:
        console.print(f"[red]Error: --era must be one of: {', '.join(ERAS)}[/red]")
        raise typer.Exit(code=1)

    configure_logging(level="DEBUG" if verbose else "INFO")

    if era == "pre2018":
        # The post-2018 MIN_YEAR/MAX_YEAR defaults do not apply to this era.
        pre_lo, pre_hi = HMDAFHFAMatchingConfig.ERA_YEARS["pre2018"]
        lo = min_year if min_year != MIN_YEAR else pre_lo
        hi = max_year if max_year != MAX_YEAR else pre_hi
        try:
            console.print(f"[cyan]Running HMDA-FHFA pre-2018 matching for {lo}-{hi}...[/cyan]")
            stats = run_matching_pre2018(
                years=list(range(lo, hi + 1)), save=save, overwrite=overwrite
            )
            console.print("\n[green]HMDA-FHFA pre-2018 matching complete![/green]")
            for key, value in stats.items():
                console.print(f"  {key}: {value}")
        except Exception as e:
            console.print(f"[red]HMDA-FHFA pre-2018 matching failed: {e}[/red]")
            raise typer.Exit(code=1)
        return

    years = list(range(min_year, max_year + 1))

    try:
        console.print(f"[cyan]Running HMDA-FHFA matching for {min_year}-{max_year}...[/cyan]")
        stats = run_matching(years=years, save=save)

        console.print("\n[green]HMDA-FHFA matching complete![/green]")
        if isinstance(stats, dict):
            for key, value in stats.items():
                console.print(f"  {key}: {value}")

    except Exception as e:
        console.print(f"[red]HMDA-FHFA matching failed: {e}[/red]")
        raise typer.Exit(code=1)


@app.command()
def validate(
    era: str = typer.Option(
        "post2018",
        "--era",
        help=_ERA_HELP,
    ),
    show_plots: bool = typer.Option(
        False,
        "--show-plots",
        "-s",
        help="Show plots interactively",
    ),
    save_plots: bool = typer.Option(
        True,
        "--save/--no-save",
        help="Save plots to docs/matching/figures/",
    ),
) -> None:
    """Run validation analysis on HMDA-FHFA matches, for one era.

    Each era writes one combined figure plus a state choropleth (plotly, so it
    cannot share the grid) into its own figure directory, which is what
    docs/matching/hmda_fhfa_matching.md embeds.

    --era post2018 (default) panels: match rates by year, by loan amount, by
    interest rate, state size vs match rate, and the enterprise breakdown
    (FNMA vs FHLMC).

    --era pre2018 (2008-2017) panels: match rates by year and by loan amount,
    plus the era's own evidence — round contribution by year, the
    demographic-agreement-vs-random baseline, and the AMFI-inflation
    diagnostic. There is no interest-rate panel; pre-2018 FHFA carries no rate.

    Examples:
      mortgage-data match hmda-fhfa validate
      mortgage-data match hmda-fhfa validate --era pre2018
      mortgage-data match hmda-fhfa validate --show-plots
      mortgage-data match hmda-fhfa validate --no-save
    """
    from mortgage_data_manager.matching.match_hmda_fhfa.validation import (
        run_validation,
        run_validation_pre2018,
    )

    if era not in ERAS:
        console.print(f"[red]Error: --era must be one of: {', '.join(ERAS)}[/red]")
        raise typer.Exit(code=1)

    runner = run_validation_pre2018 if era == "pre2018" else run_validation

    try:
        console.print(f"[cyan]Running HMDA-FHFA validation analysis ({era})...[/cyan]")

        results = runner(save_plots=save_plots, show_plots=show_plots)

        if "error" in results:
            console.print(f"[red]Validation error: {results['error']}[/red]")
            raise typer.Exit(code=1)

        console.print("[green]Validation complete![/green]")

    except Exception as e:
        console.print(f"[red]Validation failed: {e}[/red]")
        raise typer.Exit(code=1)


def cli_main() -> None:
    """Entry point for HMDA-FHFA matching CLI."""
    app()


if __name__ == "__main__":
    cli_main()
