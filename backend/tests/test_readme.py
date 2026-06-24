from pathlib import Path


def test_readme_contains_payment_monitor_invoice_database_warning():
    readme = Path(__file__).resolve().parents[2] / "README.md"
    text = readme.read_text(encoding="utf-8")

    assert "Payment Monitor is not an invoice database." in text
    assert "does not create, reserve, store, or expire invoices server-side" in text
    assert "Each payment should use a unique receiving address." in text
    assert "Reusing addresses can cause ambiguous payment detection." in text


def test_readme_documents_payment_response_fields():
    readme = Path(__file__).resolve().parents[2] / "README.md"
    text = readme.read_text(encoding="utf-8")

    for field in [
        "requested_amount",
        "requested_sats",
        "confirmed_received_sats",
        "mempool_received_sats",
        "total_received_sats",
        "status_explanation",
        "explorer_address_url",
    ]:
        assert field in text

    assert "Amounts are address-level totals." in text
    assert "Mempool values are unconfirmed." in text
    assert "PEPEW_MIN_CONFIRMATIONS" in text
    assert "not a merchant invoice ledger" in text
