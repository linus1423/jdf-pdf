"""Kommandozeile für Stapelverarbeitung: JDF in viele PDFs einbetten."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .core.jdf import ColorModel, JobTicket, Sides
from .core.prepress import process_file


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jdfpdf-cli", description="JDF in PDFs einbetten")
    parser.add_argument("pdfs", nargs="+", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True, help="Zielordner")
    parser.add_argument("--copies", type=int, default=1)
    parser.add_argument("--sides", choices=[s.name.lower() for s in Sides], default="simplex")
    parser.add_argument("--color", choices=[c.name.lower() for c in ColorModel], default="cmyk")
    parser.add_argument("--weight", type=float, help="Grammatur in g/m²")
    parser.add_argument("--no-sidecar", action="store_true", help="keine separate .jdf-Datei schreiben")
    args = parser.parse_args(argv)

    template = JobTicket(
        job_name="",
        pdf_url="",
        copies=args.copies,
        sides=Sides[args.sides.upper()],
        color=ColorModel[args.color.upper()],
        media_weight_gsm=args.weight,
    )
    failed = 0
    for pdf in args.pdfs:
        try:
            result = process_file(pdf, args.output, template, write_sidecar=not args.no_sidecar)
        except Exception as exc:  # ein kaputtes PDF soll den Stapel nicht abbrechen
            failed += 1
            print(f"FEHLER {pdf}: {exc}", file=sys.stderr)
            continue
        print(f"OK     {result.output}")
        for warning in result.warnings:
            print(f"       Warnung: {warning}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
