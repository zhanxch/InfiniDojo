"""Command dispatch; native execution must use the Isaac runtime Python."""

import argparse
import importlib
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["assets", "frontend", "native", "study", "evaluation"])
    args, rest = parser.parse_known_args()
    sys.argv = [f"infinidojo {args.command}", *rest]
    importlib.import_module(f"infinidojo.{args.command}").main()


if __name__ == "__main__":
    main()
