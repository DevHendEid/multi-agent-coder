from __future__ import annotations

from pathlib import Path

calc = Path("calc.py")
if calc.exists():
    text = calc.read_text()
    if "def divide" not in text:
        calc.write_text(text.rstrip() + "\n\n\ndef divide(a: int, b: int) -> float:\n    if b == 0:\n        raise ValueError('cannot divide by zero')\n    return a / b\n")

test_calc = Path("test_calc.py")
if test_calc.exists():
    text = test_calc.read_text()
    if "def test_divide" not in text:
        test_calc.write_text(text.rstrip() + "\n\n\ndef test_divide():\n    from calc import divide\n    assert divide(6, 2) == 3.0\n")
