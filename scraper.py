#!/usr/bin/env python3
"""
JanNidhi Sentinel - Standalone MPLADS Scraper CLI Entrypoint.

Usage:
    python scraper.py
    python scraper.py --batch-size 5000
    python scraper.py --reset-checkpoint
    python scraper.py --house lok_sabha
    python scraper.py --source-file data/mplads_raw_sample.csv
"""
import sys
from pathlib import Path

# Ensure root directory is on Python path
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.services.scraper import main

if __name__ == "__main__":
    main()
