from parser import parse_items, recover_accents

CORRUPT = "�"


def receipt(*spans):
    return {"htmlPrintedReceipt": "\n".join(spans)}


def article(desc, text, art_id="1", unit="2,09", qty=None):
    q = f' data-art-quantity="{qty}"' if qty else ""
    return (f'<span id="l" class="article" data-art-id="{art_id}" data-unit-price="{unit}"'
            f'{q} data-art-description="{desc}">{text}</span>')


def test_recovers_accent_from_visible_line():
    d = receipt(article(f"Brioche tranch{CORRUPT}e",
                        "Brioche tranch&eacute;e       2,09  1    2,09 A T"))
    assert parse_items(d)[0]["name"] == "Brioche tranchée"


def test_recovers_symbols_and_several_characters():
    assert recover_accents(f"Pomme Pink Lady{CORRUPT}", "Pomme Pink Lady®       2,99  1") == "Pomme Pink Lady®"
    assert recover_accents(f"Poivre n{CORRUPT}7", "Poivre n°7      2,89") == "Poivre n°7"
    assert recover_accents(f"Lait 1/2 {CORRUPT}cr{CORRUPT}m{CORRUPT} UHT", "Lait 1/2 écrémé UHT    0,99") == "Lait 1/2 écrémé UHT"


def test_recovers_when_printed_name_is_truncated():
    assert (recover_accents(f"Yaourts brass{CORRUPT}s aux fruits", "Yaourts brassés aux    3,26  2    6,52 A T")
            == "Yaourts brassés aux fruits")


def test_untouched_when_nothing_to_fix_or_alignment_is_unsure():
    assert recover_accents("Lait", "Autre chose   1,00") == "Lait"
    # le caractère aligné serait un chiffre : on ne devine pas
    assert recover_accents(f"Pain{CORRUPT}", "Pain5     1,00") == f"Pain{CORRUPT}"
    assert recover_accents(f"Pain{CORRUPT}", "Tout autre texte") == f"Pain{CORRUPT}"


def test_weighed_product_name_comes_from_the_second_line_without_duplicate():
    d = receipt(
        article(f"P{CORRUPT}che vrac", "  0,652 kg x 3,99   EUR/kg", art_id="9", unit="3,99", qty="0,652"),
        article(f"P{CORRUPT}che vrac", "P&ecirc;che vrac        2,60", art_id="9", unit="3,99", qty="0,652"),
    )
    items = parse_items(d)
    assert [i["name"] for i in items] == ["Pêche vrac"]
