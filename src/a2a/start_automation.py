#!/usr/bin/env python3
# A2A Automation Service Launcher
import os
import sys

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if __name__ == '__main__':
    from automated_main import main
    main()
