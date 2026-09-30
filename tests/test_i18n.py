import ast
import pathlib
import re

from jdfpdf.ui import i18n

SRC = pathlib.Path(i18n.__file__)


def test_no_duplicate_keys_and_both_languages():
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "_TEXTS":
            keys = [k.value for k in node.value.keys]
            assert len(keys) == len(set(keys)), [k for k in keys if keys.count(k) > 1]
    for key, entry in i18n._TEXTS.items():
        assert set(entry) == {"de", "en"}, key


def test_keys_used_in_ui_exist():
    used = set()
    for path in SRC.parent.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        used |= set(re.findall(r'(?:tr_?|self\.tr_|self\._tr|row)\("([a-z0-9_]+)"', text))
        used |= set(re.findall(r'bind\([^,]+, "([a-z0-9_]+)"', text))
        used |= set(re.findall(r'action\("([a-z0-9_]+)"', text))
    missing = sorted(k for k in used if k not in i18n._TEXTS)
    assert not missing, missing


def test_enum_labels_exist():
    from jdfpdf.core.elements import Anchor
    from jdfpdf.core.geometry import ScaleMode
    from jdfpdf.core.jdf import ColorModel, Fold, Punch, Sides, Staple
    from jdfpdf.core.marks import BarcodeType

    for enum, prefix in [(Sides, ""), (ColorModel, ""), (Staple, "staple"), (Punch, "punch"), (Fold, "fold"),
                         (ScaleMode, "scale"), (Anchor, "anchor"), (BarcodeType, "barcode")]:
        for member in enum:
            key = f"{prefix}_{member.name.lower()}" if prefix else member.name.lower()
            assert key in i18n._TEXTS, key
