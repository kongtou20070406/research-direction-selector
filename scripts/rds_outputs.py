"""Safe output-directory contract for project generators.

RDS creates the parents of declared output files before invoking project code.
Generators may accept that empty directory but must reject existing user data.
This helper grants no path authority; the caller supplies its authorized path.
"""
from pathlib import Path


def prepare_empty_directory(destination):
    path = Path(destination)
    if path.is_symlink():
        raise FileExistsError('Output directory is a symbolic link: ' + str(path))
    try:
        path.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        if not path.is_dir() or next(path.iterdir(), None) is not None:
            raise FileExistsError('Output directory must be absent or empty: ' + str(path)) from None
    return path
