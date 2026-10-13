import argparse
import asyncio
import json
from pathlib import Path

from dotenv import load_dotenv

from casedesk.config import Settings
from casedesk.engine import Engine
from casedesk.evaluation import evaluate
from casedesk.providers import ProviderError
from casedesk.retrieval import KnowledgeBase
from casedesk.store import Store


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(
        prog="casedesk", description="Grounded support triage with local simulated approvals"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="Start the local support inbox")
    serve.add_argument("--port", type=int, default=8012)
    demo = commands.add_parser(
        "demo", help="Offline draft only: never invokes a model or approves an action"
    )
    demo.add_argument("--ticket", default="CS-1001")
    demo.add_argument("--output", type=Path)
    evaluation = commands.add_parser(
        "eval", help="Nine offline synthetic regression cases in a temporary database"
    )
    evaluation.add_argument("--output", type=Path, default=Path("artifacts/eval-results.json"))
    args = parser.parse_args()
    try:
        if args.command == "serve":
            import uvicorn

            from casedesk.api import create_app

            uvicorn.run(
                create_app(Settings.from_env()), host="127.0.0.1", port=args.port, workers=1
            )
        elif args.command == "eval":
            result = asyncio.run(evaluate(args.output))
            print(json.dumps(result, ensure_ascii=False, indent=2))
            raise SystemExit(0 if result["passed"] == result["total"] else 1)
        else:
            # Demo stays offline even when a user's .env selects live mode.
            import os

            settings = Settings(data_dir=Path(os.getenv("CASEDESK_DATA_DIR", ".casedesk")))
            store = Store(settings.data_dir / "casedesk.sqlite3")
            result = asyncio.run(Engine(settings, store, KnowledgeBase()).analyze(args.ticket))
            text = json.dumps(result, ensure_ascii=False, indent=2)
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(text, "utf-8")
            print(text)
            if result["status"] == "failed":
                raise SystemExit(1)
    except KeyError:
        parser.exit(1, "casedesk: unknown ticket\n")
    except (ValueError, OSError, ProviderError) as exc:
        parser.exit(1, f"casedesk: {exc}\n")


if __name__ == "__main__":
    main()
