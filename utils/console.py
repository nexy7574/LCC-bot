import shutil

from rich.console import Console

_col, _ = shutil.get_terminal_size((80, 20))
if _col == 80:
    _col = 200

__all__ = ("console",)

console = Console(width=_col, soft_wrap=True, tab_size=4)
