from pathlib import Path


def test_readme_contains_payment_monitor_invoice_database_warning():
    readme = Path(__file__).resolve().parents[2] / "README.md"
    text = readme.read_text(encoding="utf-8")

    assert "Payment Monitor is not an invoice database." in text
    assert "does not create, reserve, store, or expire invoices server-side" in text
    assert "Each payment should use a unique receiving address." in text
    assert "Reusing addresses can cause ambiguous payment detection." in text
