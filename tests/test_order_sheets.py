from datetime import datetime

import openpyxl

from resell.importers.order_sheets import read_orders


def _wb():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Order #1"
    ws.append([None, "Article", "Date", "Amount purchased", "Amount Yuan", "Overall EURO", "Add costs", "Shipping", "Overall"])
    ws.append([None, "N3DSLL dual IPS white 98", datetime(2026, 1, 3), 1, 1600, 200, 0.5, 8, 208.5])
    ws.append([None, "PSV 64GB card", datetime(2026, 1, 4), 4, 255, 122.89, 0, 4, 126.89])
    ws.append([None, None, None, None, None, 322.89, 0.5, 12, 335.39])  # totals row
    ws.append([None, "Sub heading only"])
    ws2 = wb.create_sheet("Order #2")  # headerless, link-first layout
    ws2.append(["https://www.goofish.com/item?spm=a21ybx.personal.feeds.1.58496ac2x&id=1", "Ipod 7 1T",
                datetime(2026, 2, 1), 1, 1600, 200, 0.5, 4, 204.5, "Sent out 02.02"])
    return wb


def test_read_orders():
    df, issues = read_orders(_wb(), "Order #")
    assert len(df) == 3
    card = df[df.item == "PSV 64GB card"].iloc[0]
    assert round(card.eff_rate, 2) == round(4 * 255 / 122.89, 2)  # unit RMB x qty / line EUR
    ipod = df[df.item == "Ipod 7 1T"].iloc[0]
    assert ipod.link.startswith("https://") and ipod.note == "Sent out 02.02" and ipod.eff_rate == 8.0
    assert any("without any amount" in i["reason"] for i in issues)
