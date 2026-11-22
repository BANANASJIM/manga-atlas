"""Command line: build / serve / warm."""

from __future__ import annotations

import argparse

from manga_atlas.build import add_arguments as add_build_arguments
from manga_atlas.config import load


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="manga-atlas")
    sub = parser.add_subparsers(dest="cmd", required=True)
    build = sub.add_parser("build", help="download the latest Bangumi archive and rebuild atlas.db")
    add_build_arguments(build)
    sub.add_parser("serve", help="serve the web UI")
    warm = sub.add_parser("warm", help="pre-check popular works through the running app")
    warm.add_argument("--top", type=int, default=200)
    args = parser.parse_args(argv)
    settings = load()

    if args.cmd == "serve":
        import uvicorn

        from manga_atlas.web.app import create_app

        uvicorn.run(create_app(settings), host=settings.host, port=settings.port)
        return 0
    if args.cmd == "build":
        from manga_atlas.build import run

        return run(settings, dump=args.dump, force=args.force)
    from manga_atlas.availability import warm as run_warm

    return run_warm(settings, top=args.top)
