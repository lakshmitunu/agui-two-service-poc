import pytest

from flag_non_english import check_feedback

ENGLISH_OK = [
    "The product arrived broken and nobody helped me.",
    "Great service, I will buy again. 10/10 https://example.com",
    "Thanks for everything",
    "The hotel has internet and wifi, all excellent.",
    "I ordered from Amazon and got a WhatsApp message, ok.",
    "",
    None,
    "12345 !!! 😀",
    "Great service, thanks! - Maria Gonzalez",
    "The product arrived on time. Regards, José Hernández",
    "Thank you Carlos Rodríguez for the help",
    "Excellent support from Sofía Martínez, very helpful",
    "Thanks Pedro",
    "Great job, Luis Fernando García López",
    "My name is Juan Carlos de la Cruz and the product was great",
    "Thanks, Ana María",
]

FLAGGED = [
    ("El producto llegó roto y nadie me ayudó.", "SPANISH"),
    ("The service is excellent, pero la entrega fue muy lenta.", "SPANISH"),
    ("I want my money back porque el pedido llegó tarde.", "SPANISH"),
    ("I love it, pero the shipping was slow", "SPANISH"),
    ("Le produit est arrivé cassé, très déçu.", "FRENCH"),
    ("Das Produkt war kaputt. Sehr schlecht.", "GERMAN"),
    ("Great service. 非常好", None),
    ("Thanks Pedro. Order delivered fast. Gracias", "SPANISH"),
    ("Thanks José, pero el pedido llegó roto", "SPANISH"),
    ("Great, EL PRODUCTO LLEGÓ ROTO Y NADIE ME AYUDÓ", "SPANISH"),
    ("Отличный сервис, спасибо", None),
]


@pytest.mark.parametrize("text", ENGLISH_OK)
def test_english_not_flagged(text):
    r = check_feedback(text)
    assert not r.flagged, r


@pytest.mark.parametrize("text,lang", FLAGGED)
def test_non_english_flagged(text, lang):
    r = check_feedback(text)
    assert r.flagged, r
    if lang:
        assert lang in r.languages, r


def test_never_raises_on_odd_input():
    for v in (123, 4.5, float("nan"), b"bytes", ["a"], "\x00\x00"):
        check_feedback(v)


def test_json_output():
    import json

    d = json.loads(check_feedback("Great service, pero la entrega fue lenta").to_json())
    assert d["flagged"] is True
    assert set(d) == {"flagged", "languages", "reasons", "reason_descriptions", "evidence", "error"}
    assert len(d["reasons"]) == len(d["reason_descriptions"])
