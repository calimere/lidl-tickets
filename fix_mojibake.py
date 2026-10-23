"""Nettoyage best-effort du caractère de remplacement Unicode � dans les
libellés produit tronqués qu'on ne peut pas enrichir via Open Food Facts.

Le � remplace une lettre accentuée perdue côté Lidl : on ne peut pas savoir
avec certitude laquelle (irrécupérable). On tente quand même de deviner en
cherchant, dans le vocabulaire français déjà présent dans notre propre base
(nos noms de tickets propres + les libellés Open Food Facts trouvés), un mot
de même longueur qui correspond lettre à lettre en dehors des �. Si aucun mot
ne correspond, on se contente de retirer le caractère (le nom reste lisible,
juste sans l'accent).

Usage : python manage.py fixnames
"""
from __future__ import annotations

import re
from collections import Counter

import db

WORD_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ�]+")

# Par défaut si rien de mieux : "e" est de loin la lettre la plus souvent
# accentuée en français (é/è/ê/ë), donc le plus lisible en absence de piste.
# Une lettre seule isolée (espaces des deux côtés) est presque toujours "à".
FALLBACK_LETTER = "e"
FALLBACK_SINGLE = "à"


def _words(name: str) -> list[str]:
    return WORD_RE.findall(name or "")


def _build_vocabularies() -> tuple[Counter, Counter]:
    """Vocabulaire primaire = nos propres tickets (français garanti).
    Secondaire = libellés Open Food Facts (parfois en anglais) : on ne s'en
    sert qu'en dernier recours, pour éviter de deviner un mot anglais."""
    primary, secondary = Counter(), Counter()
    with db.connect() as con:
        for (name,) in con.execute("SELECT DISTINCT name FROM items WHERE name NOT LIKE '%�%'"):
            for w in _words(name):
                primary[w] += 1
        for (label,) in con.execute("SELECT DISTINCT label FROM product_labels WHERE status = 'matched'"):
            for w in _words(label):
                secondary[w] += 1
    return primary, secondary


def _pattern_for(word: str) -> re.Pattern:
    parts = ["." if ch == "�" else re.escape(ch) for ch in word]
    return re.compile("^" + "".join(parts) + "$", re.IGNORECASE)


def _best_candidate(word: str, vocab: Counter) -> str | None:
    pattern = _pattern_for(word)
    candidates = [(count, w) for w, count in vocab.items() if pattern.match(w)]
    if not candidates:
        return None
    candidates.sort(key=lambda c: -c[0])
    return candidates[0][1]


def _fix_word(word: str, primary: Counter, secondary: Counter) -> tuple[str, bool]:
    if "�" not in word:
        return word, False
    if word == "�":
        # Une seule lettre accentuée isolée : deviner au cas par cas serait
        # trop bruité (trop de mots d'une lettre coïncident), "à" couvre
        # l'immense majorité des cas réels ("yaourt � la vanille", ...).
        return FALLBACK_SINGLE, True
    best = _best_candidate(word, primary) or _best_candidate(word, secondary)
    if best:
        return best, True
    return word.replace("�", FALLBACK_LETTER), False


def fix_name(name: str, primary: Counter, secondary: Counter) -> tuple[str, bool]:
    guessed_any = False

    def repl(m: re.Match) -> str:
        nonlocal guessed_any
        fixed, guessed = _fix_word(m.group(), primary, secondary)
        guessed_any = guessed_any or guessed
        return fixed

    return WORD_RE.sub(repl, name), guessed_any


def fix_all() -> dict:
    primary, secondary = _build_vocabularies()
    rows = db.codes_with_corrupted_name()
    guessed = stripped = 0
    for code, name in rows:
        fixed, did_guess = fix_name(name, primary, secondary)
        db.save_name_fix(code, fixed, "guess" if did_guess else "strip")
        if did_guess:
            guessed += 1
        else:
            stripped += 1
    return {"total": len(rows), "guessed": guessed, "stripped": stripped}
