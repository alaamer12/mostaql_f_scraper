"""
Deprecated — use followup.py instead.

    python followup.py
    python followup.py --limit 50
"""

import sys

if __name__ == "__main__":
    print("rescue_nulls.py is deprecated. Use:  python followup.py")
    sys.argv = ["followup.py"] + sys.argv[1:]
    from followup import main
    main()
