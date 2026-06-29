"""`python -m job_search` -> the scraper CLI (interactive menu by default)."""
from .scraper_cli import main

if __name__ == "__main__":
    raise SystemExit(main())
