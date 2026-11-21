"""PyInstaller's entry script for Hidee.app; the logic lives in hidee/frozen.py."""
import sys

from hidee.frozen import main

sys.exit(main())
