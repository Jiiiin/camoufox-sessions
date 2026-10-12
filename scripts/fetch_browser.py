"""Fetch the library's paired browser into an isolated build cache."""
from pathlib import Path
import sys
import platformdirs

cache = Path(sys.argv[1]).resolve()
cache.mkdir(parents=True, exist_ok=True)
original = platformdirs.user_cache_dir
platformdirs.user_cache_dir = lambda name=None, *a, **k: (
    str(cache) if name == 'camoufox' else original(name, *a, **k)
)
# Import after redirecting the cache. The Camoufox CLI may clean incompatible
# data there, so never point this helper at a user's existing browser cache.
from camoufox.__main__ import cli
from camoufox.pkgman import launch_path

cli.main(args=['fetch'], standalone_mode=False)
if not Path(launch_path()).is_file():
    raise SystemExit('Browser download did not produce an executable.')
