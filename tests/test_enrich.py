"""Unit tests on made-up rows - no personal data in the repo."""
from pathlib import Path

import pytest

from resell import enrich
from resell.config import load_config

CFG = load_config(Path(__file__).resolve().parents[1] / "config.yaml")


@pytest.mark.parametrize("name,cat", [
    ("N3DSLL dual IPS white 98", "N3DS"),
    ("Hori MH 3DS Grip", "Grip"),
    ("Hori PSV 2000 grip", "Grip"),
    ("Vita 64GB card", "Vita card"),
    ("PSV 32G", "Vita card"),
    ("Ipod 7 1T", "iPod"),
    ("Ugreen Switch Bluetooth Adapter", "Bluetooth"),
    ("Lexar Ares 6800 ddr5", "DDR5"),
    ("AYN THOR Grey 16+1TB", "AYN"),
    ("Mew Golden GBC", "GB/GBA"),
    ("Olympus Mju II 35mm f2.8 (95%)", "Camera"),
    ("Pokemon Espeon (JP PSA9)", "Other"),
])
def test_categorize(name, cat):
    assert enrich.categorize(name, CFG) == cat


@pytest.mark.parametrize("name,grade", [
    ("N3DSLL dual IPS white 98", 98),
    ("N3DSLL dual IPS grey 99 (16gb)", 99),
    ("N3DSLL RED DUAL 1", None),
    ("Ipod 7 512G", None),
    ("GSkill Trident Z5 RGP DDR5 16x2 6000", None),
    ("N3DSLL dual IPS blue (might be grey)94", 94),
])
def test_grade(name, grade):
    assert enrich.grade_of(name, CFG) == grade


def test_flaws_major_and_minor():
    groups, words, minor = enrich.detect_flaws("Slightly yellow right side + dust under top screen", CFG)
    assert groups == ["yellow", "dust_debris"] and not minor
    groups, _, minor = enrich.detect_flaws("Scratches on screen (not too bad - wont mention)", CFG)
    assert groups == ["scratch"] and minor
    # yellow and dust are always real flaws, however mildly worded
    assert enrich.detect_flaws("Yellow corners (not too bad - wont mention)", CFG)[2] is False
    assert enrich.detect_flaws("tiny spider thing", CFG)[2] is False
    assert enrich.detect_flaws("Had to take my own sd card (10€ added)", CFG)[0] == []
    assert enrich.detect_flaws("Immaculate (can be sold super pricey)", CFG)[0] == []


def test_parse_source():
    url = ("https://www.goofish.com/item?spm=a21ybx.personal.feeds.2.58496ac2ZTizHi"
           "&id=1043700970691&categoryId=50025395")
    p = enrich.parse_source(url)
    assert p["storefront"] == "5849" and p["listing_id"] == "goofish:1043700970691"
    assert enrich.parse_source("中古数码 - Ipod 1T")["name"] == "中古数码"
    assert enrich.parse_source("new3dsll日版 新大三_闲鱼")["kind"] == "listing_title"
    assert enrich.parse_source(float("nan"))["kind"] == "none"


def test_part_vs_unit():
    assert enrich._kind("N3DS IPS lower screen") == "part"
    assert enrich._kind("Smashbros N3DS (upper screen broken)") == "unit"
    assert enrich._kind("N3DSXL with upper IPS screen") == "unit"
    assert enrich._kind("PS Vita SD card 32GB") == "unit"
