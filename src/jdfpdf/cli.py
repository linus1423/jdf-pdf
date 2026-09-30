"""Kommandozeile: Stapelverarbeitung, Vorlagen, Hotfolder, Drucken und JMF.

    jdfpdf-cli a.pdf b.pdf -o out [Optionen]         PDFs mit JDF ausgeben
    jdfpdf-cli run vorlage.jdftpl *.pdf -o out -p copies=5
    jdfpdf-cli hotfolder vorlage.jdftpl --in eingang --out ausgang [--printer NAME]
    jdfpdf-cli send DRUCKER a.pdf [--template vorlage.jdftpl]
    jdfpdf-cli queue http://controller:8010/jmf       JMF-Warteschlange abfragen
    jdfpdf-cli printers                              Systemdrucker auflisten
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .core import ppf
from .core.jdf import ColorModel, Finishing, Fold, JobTicket, Punch, Sides, Staple
from .core.media import MediaCatalog
from .core.prepress import OutputOptions, PdfxPolicy, process_file


def _choices(enum) -> list[str]:
    return [e.name.lower() for e in enum]


SUBCOMMANDS = ("run", "hotfolder", "send", "queue", "printers")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in SUBCOMMANDS:
        return _subcommand(argv)
    return _process(argv)


def _print_result(result) -> None:
    print(f"OK     {result.pdf}")
    for extra in result.extra_files:
        print(f"       + {extra}")
    for warning in result.warnings:
        print(f"       Warnung: {warning}")


def _params(values: list[str]) -> dict:
    from .core.template import parse_param

    return dict(parse_param(v) for v in values or [])


def _printer(name: str, path: Path | None):
    from .core.printing import load_printers

    profile = next((p for p in load_printers(path) if p.name == name), None)
    if profile is None:
        raise SystemExit(f"Druckerprofil {name!r} nicht gefunden")
    return profile


def _subcommand(argv: list[str]) -> int:
    from .core.template import Template, apply_template, run_template

    parser = argparse.ArgumentParser(prog="jdfpdf-cli")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Vorlage auf Dateien anwenden")
    run.add_argument("template", type=Path)
    run.add_argument("files", nargs="+", type=Path)
    run.add_argument("-o", "--output", type=Path, required=True)
    hot = sub.add_parser("hotfolder", help="Eingangsordner überwachen")
    hot.add_argument("template", type=Path)
    hot.add_argument("--in", dest="inbox", type=Path, required=True)
    hot.add_argument("--out", dest="outbox", type=Path, required=True)
    hot.add_argument("--interval", type=float, default=2.0)
    hot.add_argument("--once", action="store_true", help="nur zwei Durchläufe (zum Testen)")
    hot.add_argument("--printer", help="Ergebnis zusätzlich an dieses Druckerprofil senden")
    send = sub.add_parser("send", help="Dateien an ein Druckerprofil senden")
    send.add_argument("printer")
    send.add_argument("files", nargs="+", type=Path)
    send.add_argument("--template", type=Path)
    send.add_argument("--copies", type=int)
    queue = sub.add_parser("queue", help="JMF-Warteschlange abfragen")
    queue.add_argument("url")
    sub.add_parser("printers", help="Systemdrucker auflisten")
    for p in (run, hot, send):
        p.add_argument("-p", "--param", action="append", default=[], help="Parameter name=wert")
        p.add_argument("--catalog", type=Path, help="Medienkatalog (JSON)")
    for p in (hot, send):
        p.add_argument("--printers", type=Path, help="Druckerprofile (JSON)")
    args = parser.parse_args(argv)

    if args.command == "printers":
        from .core.printing import list_system_printers

        for name in list_system_printers():
            print(name)
        return 0
    if args.command == "queue":
        from .core.jmf import queue_status

        response = queue_status(args.url)
        print(f"ReturnCode {response.return_code} {response.queue_status} {response.comment}".strip())
        for entry in response.entries:
            print(f"{entry.queue_entry_id}\t{entry.status}\t{entry.job_id}")
        return 0 if response.ok else 1

    catalog = MediaCatalog.load(args.catalog)
    overrides = _params(args.param)
    if args.command == "run":
        template = Template.load(args.template)
        failed = 0
        for path in args.files:
            try:
                _print_result(run_template(path, args.output, template, overrides, catalog,
                                           base_dir=args.template.parent))
            except Exception as exc:
                failed += 1
                print(f"FEHLER {path}: {exc}", file=sys.stderr)
        return 1 if failed else 0

    from .core.pdfdoc import PdfDocument
    from .core.printing import send as send_to

    if args.command == "hotfolder":
        from .core.hotfolder import Hotfolder

        printer = _printer(args.printer, args.printers) if args.printer else None

        def after(path, result) -> None:
            if printer is not None:
                send_to(printer, PdfDocument.open(result.pdf), result.ticket, name=path.stem)

        template = Template.load(args.template)
        folder = Hotfolder(args.inbox, args.outbox, template, overrides=overrides, catalog=catalog, after=after)
        print(f"Hotfolder {args.inbox} -> {args.outbox} (Strg+C beendet)")
        rounds = [0]

        def stop() -> bool:
            rounds[0] += 1
            return args.once and rounds[0] > 2

        try:
            folder.run(0 if args.once else args.interval, stop, lambda e: print(e.line()))
        except KeyboardInterrupt:
            pass
        return 0

    # send
    profile = _printer(args.printer, args.printers)
    template = Template.load(args.template) if args.template else Template()
    failed = 0
    for path in args.files:
        try:
            doc = PdfDocument.open(path)
            ticket, output, imposition, _ = apply_template(doc, template, path, overrides, catalog)
            if args.copies:
                ticket.copies = args.copies
            result = send_to(profile, doc, ticket, output, imposition, name=path.stem)
            print(f"OK     {path} -> {result.profile}: {result.message}")
        except Exception as exc:
            failed += 1
            print(f"FEHLER {path}: {exc}", file=sys.stderr)
    return 1 if failed else 0


def _process(argv: list[str]) -> int:
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
    parser.add_argument("--preflight", action="store_true", help="Preflight-Bericht <name>_preflight.html schreiben")
    parser.add_argument("--ppf", action="store_true", help="CIP3-PPF je Bogen und Farbzonen-CSV schreiben")
    parser.add_argument("--ppf-embed", action="store_true", help="PPF ins PDF einbetten")
    parser.add_argument("--press", help="Name des Maschinenprofils für die Farbzonen")
    parser.add_argument("--presses", type=Path, help="Maschinenprofile (JSON), sonst Standardprofile")
    parser.add_argument("--finishing-jdf", action="store_true", help="eigenes JDF für die Weiterverarbeitung")
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
    profile = None
    if args.press:
        profile = next((p for p in ppf.load_profiles(args.presses) if p.name == args.press), None)
        if profile is None:
            parser.error(f"Maschinenprofil {args.press!r} nicht gefunden")
    options = OutputOptions(
        embed=not args.no_embed,
        sidecar=not args.no_sidecar,
        ticketing=args.ticketing,
        pdfx_policy=PdfxPolicy.EMBED_ANYWAY if args.embed_into_pdfx else PdfxPolicy.KEEP,
        ppf=args.ppf,
        ppf_embed=args.ppf_embed,
        ppf_profile=profile,
        preflight=args.preflight,
        finishing_jdf=args.finishing_jdf,
    )
    if not options.any_output:
        parser.error("Keine Ausgabe gewählt")

    failed = 0
    for pdf in args.pdfs:
        try:
            result = process_file(pdf, args.output, template, options)
        except Exception as exc:  # ein kaputtes PDF soll den Stapel nicht abbrechen
            failed += 1
            print(f"FEHLER {pdf}: {exc}", file=sys.stderr)
            continue
        _print_result(result)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
