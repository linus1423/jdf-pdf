"""Kommandozeile für Stapelverarbeitung: JDF erzeugen und mit PDFs ausgeben."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .core.jdf import ColorModel, Finishing, Fold, JobTicket, Punch, Sides, Staple
from .core.media import MediaCatalog
from .core.prepress import OutputOptions, PdfxPolicy, process_file


def _choices(enum) -> list[str]:
    return [e.name.lower() for e in enum]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jdfpdf-cli", description="JDF erzeugen und mit PDFs ausgeben")
    parser.add_argument("pdfs", nargs="+", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True, help="Zielordner")
    parser.add_argument("--copies", type=int, default=1)
    parser.add_argument("--sides", choices=_choices(Sides), default="simplex")
    parser.add_argument("--color", choices=_choices(ColorModel), default="cmyk")
    parser.add_argument("--media", help="Name eines Mediums aus dem Medienkatalog")
    parser.add_argument("--catalog", type=Path, help="Medienkatalog (JSON), sonst Standardkatalog")
    parser.add_argument("--staple", choices=_choices(Staple), default="none")
    parser.add_argument("--punch", choices=_choices(Punch), default="none")
    parser.add_argument("--fold", choices=_choices(Fold), default="none")
    parser.add_argument("--no-embed", action="store_true", help="JDF nicht ins PDF einbetten")
    parser.add_argument("--no-sidecar", action="store_true", help="keine separate .jdf-Datei schreiben")
    parser.add_argument("--ticketing", action="store_true", help="zusätzlich JDF+PDF in einer Datei (PRISMAsync)")
    parser.add_argument(
        "--embed-into-pdfx", action="store_true", help="auch in PDF/X-1a/3/4 einbetten (bricht ggf. die Konformität)"
    )
    args = parser.parse_args(argv)

    media = None
    if args.media:
        media = MediaCatalog.load(args.catalog).get(args.media)
        if media is None:
            parser.error(f"Medium {args.media!r} nicht im Katalog")

    template = JobTicket(
        job_name="",
        pdf_url="",
        copies=args.copies,
        sides=Sides[args.sides.upper()],
        color=ColorModel[args.color.upper()],
        media=media,
        finishing=Finishing(
            staple=Staple[args.staple.upper()], punch=Punch[args.punch.upper()], fold=Fold[args.fold.upper()]
        ),
    )
    options = OutputOptions(
        embed=not args.no_embed,
        sidecar=not args.no_sidecar,
        ticketing=args.ticketing,
        pdfx_policy=PdfxPolicy.EMBED_ANYWAY if args.embed_into_pdfx else PdfxPolicy.KEEP,
    )
    if not options.any:
        parser.error("Keine Ausgabe gewählt")

    failed = 0
    for pdf in args.pdfs:
        try:
            result = process_file(pdf, args.output, template, options)
        except Exception as exc:  # ein kaputtes PDF soll den Stapel nicht abbrechen
            failed += 1
            print(f"FEHLER {pdf}: {exc}", file=sys.stderr)
            continue
        print(f"OK     {result.pdf}")
        for extra in result.extra_files:
            print(f"       + {extra}")
        for warning in result.warnings:
            print(f"       Warnung: {warning}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
