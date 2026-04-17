"""Top-level entry point for CW2.

Mirrors CW1 ``Main.py`` convention — usable as either:
    python Main.py --mode full --env_type dev
or via Poetry:
    poetry run python Main.py --mode full
"""

from engine.runner import main

if __name__ == "__main__":
    main()
