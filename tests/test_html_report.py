import json

from tests.conftest import FakeClient
from validator import pipeline
from validator.export import report_to_dict
from validator.html_report import render_html


def test_html_embeds_report_and_document(document, passport):
    report = pipeline.run(document, FakeClient(), passport, log=lambda m: None)
    data = report_to_dict(report)
    lines = [(l.no, l.text, l.page) for l in document.lines]
    html = render_html(data, lines)
    assert html.startswith("<!DOCTYPE html>") and "<title>Уведомление о расходах по карте" in html
    payload = html.split('<script type="application/json" id="data">')[1].split("</script>")[0]
    back = json.loads(payload)
    assert back["report"]["counts"] == data["counts"]
    assert len(back["lines"]) == len(document.lines)


def test_html_payload_cannot_close_script_tag():
    html = render_html({"passport": {}}, [(1, "текст </script><b>x</b>", None)])
    payload = html.split('<script type="application/json" id="data">')[1].split("</script>")[0]
    assert json.loads(payload)["lines"][0][1] == "текст </script><b>x</b>"
