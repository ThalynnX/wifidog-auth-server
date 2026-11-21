"""Jinja2 模板环境。"""
from pathlib import Path

from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def _minute(n):
    if n is None:
        return "不限"
    if n >= 60 * 24 * 30:
        return f"{n // (60 * 24 * 30)}个月"
    if n >= 60 * 24:
        return f"{n // (60 * 24)}天"
    if n >= 60:
        return f"{n // 60}小时"
    return f"{n}分钟"


def _mb(n):
    if not n:
        return "不限"
    if n >= 1024 * 1024:
        return f"{n / 1024 / 1024:.0f}TB"
    if n >= 1024:
        return f"{n / 1024:.0f}GB"
    return f"{n}MB"


def _money(fen):
    return f"{(fen or 0) / 100:.2f}"


def _bytes(b):
    b = b or 0
    if b >= 1024 ** 3:
        return f"{b / 1024 ** 3:.2f} GB"
    if b >= 1024 ** 2:
        return f"{b / 1024 ** 2:.2f} MB"
    if b >= 1024:
        return f"{b / 1024:.1f} KB"
    return f"{b} B"


templates.env.filters["minute"] = _minute
templates.env.filters["mb"] = _mb
templates.env.filters["money"] = _money
templates.env.filters["bytes"] = _bytes
