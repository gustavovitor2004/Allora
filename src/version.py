"""
version.py

Single source of truth for the app's own version number, shown in the
About dialog and in Configurações. Bump APP_VERSION by hand whenever a
new release is published - it isn't derived automatically from anything
(there's no build step that stamps it in).

Follows Semantic Versioning (semver.org): MAJOR.MINOR.PATCH.
  - MAJOR: a change that breaks existing behavior the user depends on
    (e.g. a setting's meaning changes, a file format changes incompatibly).
  - MINOR: a new feature or tab/capability added, staying backward
    compatible (e.g. a new conversion format, a new theme).
  - PATCH: a bug fix, visual tweak, or internal change with no new
    user-facing capability.
Reset MINOR and PATCH to 0 on a MAJOR bump; reset PATCH to 0 on a MINOR
bump.
"""

APP_VERSION = "1.3.0"
