"""Экспорт отчёта в JSON и HTML."""
from __future__ import annotations

import html
import json
from pathlib import Path

from .models import SystemReport


def report_to_dict(report: SystemReport) -> dict:
    return {
        "scanned_at": report.scanned_at.isoformat(timespec="seconds"),
        "sections": [
            {
                "key": s.key,
                "title": s.title,
                "error": s.error,
                "rows": [dict(zip(s.columns, row)) for row in s.rows],
            }
            for s in report.sections
        ],
        "gpus": [
            {
                "name": g.name,
                "driver_version": g.driver_version,
                "driver_date": g.driver_date,
                "vendor": g.vendor,
                "download_url": g.download_url,
            }
            for g in report.gpus
        ],
    }


def export_json(report: SystemReport, path: Path) -> None:
    path.write_text(json.dumps(report_to_dict(report), ensure_ascii=False, indent=2), encoding="utf-8")


_CSS = """
body{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#222}
h1{font-size:22px}h2{font-size:17px;margin-top:28px;border-bottom:2px solid #3b82f6;padding-bottom:4px}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{border:1px solid #ddd;padding:5px 8px;text-align:left;vertical-align:top}
th{background:#f1f5f9}tr:nth-child(even) td{background:#fafafa}
.err{color:#b91c1c}
"""


def export_html(report: SystemReport, path: Path) -> None:
    e = html.escape
    parts = [
        "<!doctype html><html lang='ru'><head><meta charset='utf-8'>",
        f"<title>Отчёт о системе</title><style>{_CSS}</style></head><body>",
        f"<h1>Отчёт о системе — {e(report.scanned_at.strftime('%Y-%m-%d %H:%M'))}</h1>",
    ]
    for s in report.sections:
        parts.append(f"<h2>{e(s.title)}</h2>")
        if s.error:
            parts.append(f"<p class='err'>Ошибка: {e(s.error)}</p>")
            continue
        if not s.rows:
            parts.append("<p>Нет данных</p>")
            continue
        parts.append("<table><tr>" + "".join(f"<th>{e(c)}</th>" for c in s.columns) + "</tr>")
        for row in s.rows:
            parts.append("<tr>" + "".join(f"<td>{e(v)}</td>" for v in row) + "</tr>")
        parts.append("</table>")
    parts.append("</body></html>")
    path.write_text("".join(parts), encoding="utf-8")

