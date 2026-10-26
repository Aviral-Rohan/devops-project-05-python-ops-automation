from datetime import datetime

import pytest

from log_report import in_window, main, normalise_path, parse_line, summarise

GOOD = ('49.36.4.222 - - [23/Oct/2026:14:20:05 +0530] "GET /api/orders/4821?x=1 HTTP/1.1" 502 157 '
        '"-" "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)" 0.004')


def line(ip="1.2.3.4", hour=10, method="GET", path="/api/menu", status=200, rt=0.010):
    return (f'{ip} - - [23/Oct/2026:{hour:02d}:15:00 +0530] "{method} {path} HTTP/1.1" '
            f'{status} 100 "-" "curl/8.7.1" {rt:.3f}')


def test_parses_a_valid_line():
    e = parse_line(GOOD)
    assert e["ip"] == "49.36.4.222"
    assert e["method"] == "GET"
    assert e["path"] == "/api/orders/{id}"
    assert e["status"] == 502
    assert e["request_time"] == pytest.approx(0.004)
    assert e["time"].hour == 14


@pytest.mark.parametrize("bad", [
    "",
    "49.36.4.222 - - [23/Oct/2026:14:2",                       # cut short by log rotation
    "\x16\x03\x01 400 TLS handshake on plain HTTP port",          # junk from a bot
    GOOD.replace(" 0.004", ""),                                  # missing request_time
])
def test_malformed_lines_return_none(bad):
    assert parse_line(bad) is None


@pytest.mark.parametrize("raw, expected", [
    ("/api/orders/42", "/api/orders/{id}"),
    ("/api/orders/42/items", "/api/orders/{id}/items"),
    ("/api/menu?lang=en", "/api/menu"),
    ("/static/app.js", "/static/app.js"),
])
def test_normalise_path(raw, expected):
    assert normalise_path(raw) == expected


def test_summary_counts_ips_statuses_and_hourly_error_rate():
    entries = [parse_line(l) for l in [
        line(ip="1.1.1.1"), line(ip="1.1.1.1"), line(ip="2.2.2.2"),
        line(ip="3.3.3.3", hour=14, status=502), line(ip="3.3.3.3", hour=14, status=200),
    ]]
    s = summarise(entries, top=2, min_requests=1)
    assert s["total"] == 5
    assert s["top_ips"] == [("1.1.1.1", 2), ("3.3.3.3", 2)]
    assert dict(s["statuses"]) == {200: 4, 502: 1}
    assert s["error_rate"] == pytest.approx(20.0)
    assert s["hourly"]["2026-10-23 14:00"] == (2, 1, 50.0)
    assert s["hourly"]["2026-10-23 10:00"] == (3, 0, 0.0)


def test_slowest_endpoint_is_first_and_rare_endpoints_are_ignored():
    entries = [parse_line(line(method="POST", path="/api/orders", rt=0.9))] * 3 + \
              [parse_line(line(path="/api/menu", rt=0.01))] * 3 + \
              [parse_line(line(path="/rare", rt=9.0))]
    slowest = summarise(entries, min_requests=2)["slowest"]
    assert slowest[0][0] == "POST /api/orders"
    assert all(ep != "GET /rare" for ep, *_ in slowest)


def test_time_window_is_start_inclusive_end_exclusive():
    e = parse_line(line(hour=14))       # 14:15
    assert in_window(e, since=datetime(2026, 10, 23, 14, 0), until=datetime(2026, 10, 23, 15, 0))
    assert not in_window(e, since=datetime(2026, 10, 23, 14, 30))
    assert not in_window(e, until=datetime(2026, 10, 23, 14, 15))


def test_missing_file_is_handled_cleanly(caplog):
    assert main(["does-not-exist.log"]) == 2
    assert "Log file not found" in caplog.text


def test_end_to_end_on_a_small_file(tmp_path, capsys):
    f = tmp_path / "access.log"
    f.write_text("\n".join([line(), line(status=500), "garbage"]) + "\n")
    assert main([str(f)]) == 0
    out = capsys.readouterr().out
    assert "Requests analysed: 2" in out
    assert "1.2.3.4" in out