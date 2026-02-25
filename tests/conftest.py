"""
Pytest configuration for auto_memory tests.
"""

import sys
from pathlib import Path

# Add project root to sys.path so tests can import auto_memory
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))
