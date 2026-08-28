"""
version.py

Single source of truth for the app's own version number, compared against
GitHub Releases by updater.py's startup check. Bump APP_VERSION by hand
whenever a new release is published - it isn't derived automatically from
anything (there's no build step that stamps it in).
"""

APP_VERSION = "1.2.0"

GITHUB_OWNER = "gustavovitor2004"
GITHUB_REPO = "Allora"
