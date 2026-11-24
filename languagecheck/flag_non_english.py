#!/usr/bin/env python3
"""Flag customer feedback that contains ANY language other than English.

English is the only allowed language (Config.allowed_language). Mixed-language
feedback (e.g. English + Spanish in one message) is flagged too.

Three independent detectors are combined; any hit flags the feedback:

1. SCRIPT   - letters from a non-Latin script (Cyrillic, CJK, Arabic, ...).
2. SEGMENT  - lingua splits the text into per-language segments (code-switching
              between clauses/sentences) and each segment is scored.
3. TOKEN    - word-frequency check catching isolated foreign words embedded in
              an English sentence ("a Spanish word in an English sentence").

Install:  pip install lingua-language-detector wordfreq
CLI:      python flag_non_english.py feedback.csv --column comment -o flagged.csv
          python flag_non_english.py feedback.jsonl --column text --format jsonl
          cat comments.txt | python flag_non_english.py -        (one per line)
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import sys
import unicodedata
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from typing import Iterable, Iterator, Sequence

from lingua import IsoCode639_1, Language, LanguageDetector, LanguageDetectorBuilder
from wordfreq import zipf_frequency

log = logging.getLogger("flag_non_english")


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Config:
    """Tuning knobs. Each field says what it does and when to raise / lower it.

    Rule of thumb: "too many English comments flagged" (false flags) -> make the
    setting STRICTER; "foreign text slipped through" (misses) -> make it LOOSER.
    Tune on a few hundred labeled real comments, one setting at a time.
    """

    # The ONLY language that is accepted (ISO 639-1). Everything else is flagged.
    # Change only if the allowed language itself changes (e.g. "es").
    allowed_language: str = "en"

    # ---- Check 2: phrase / sentence detector (lingua) --------------------- #
    # Shortest phrase (in words) the detector may judge. Short phrases are noisy.
    #   RAISE (4-5) if short English phrases get flagged by mistake.
    #   LOWER (2)   if short foreign phrases like "muy bueno" are missed.
    segment_min_words: int = 3
    # How sure (0-1) the detector must be that a phrase is NOT English.
    #   RAISE (0.70-0.80) if English comments are flagged (fewer flags).
    #   LOWER (0.40-0.50) if foreign phrases are missed (more flags).
    segment_min_confidence: float = 0.60

    # ---- Check 3: single foreign words (wordfreq) ------------------------- #
    # Languages a word is compared against English. A word is flagged if it is
    # far more common in one of these than in English.
    #   REMOVE a language that causes noisy flags (e.g. "nl", "ca").
    #   ADD a language you actually receive (e.g. "tr", "pl").
    token_languages: tuple[str, ...] = ("es", "pt", "fr", "it", "de", "ca", "nl")
    # Shortest word checked. Very short words are ambiguous across languages.
    #   RAISE (4) if short words ("an", "pan", "con") are wrongly flagged.
    #   LOWER (2) only if you must catch tiny foreign words ("de", "la", "el").
    token_min_len: int = 3
    # How common (Zipf scale, ~1-7; +1 = 10x more frequent) a word must be in the
    # foreign language to count. Filters out rare / typo / unknown words.
    #   RAISE (4.0) if rare English words or typos are flagged.
    #   LOWER (3.0) if uncommon foreign words are missed.
    token_min_zipf: float = 3.5
    # How much MORE common the word must be in the foreign language than in the
    # allowed language. Protects words used in both (hotel, internet, taxi).
    #   RAISE (1.5-2.0) if English words that are also used abroad get flagged.
    #   LOWER (0.8)     if foreign words that are also known in English are missed.
    token_margin: float = 1.2
    # Number of foreign words needed to flag a comment.
    #   1 = flag even a single foreign word (matches "even mixed text is flagged").
    #   RAISE (2-3) only if single stray words cause too many false flags.
    token_min_hits: int = 1

    # ---- Exceptions ------------------------------------------------------- #
    # Words that NEVER flag (brands, product names, jargon, loanwords).
    # ADD here whenever a legitimate word keeps being flagged. Lowercase only.
    allowlist: frozenset[str] = frozenset(
        {
            "wifi", "ok", "okay", "app", "apps", "email", "online", "whatsapp",
            "iphone", "android", "ios", "youtube", "facebook", "instagram",
            "google", "amazon", "paypal", "samsung", "netflix", "spotify",
            "pdf", "sms", "gps", "usb", "tv", "vip",
        }
    )
    # Ignore capitalized words in the middle of a sentence (likely names, e.g.
    # "Thanks, José García").
    #   TURN OFF if foreign text written in Title Case is being missed.
    ignore_proper_nouns: bool = True
    # A run of more capitalized words than this is treated as Title Case text,
    # not a name, and is still checked.
    #   RAISE (5-6) if long names are flagged.
    #   LOWER (2-3) if Title Case foreign phrases hide as names.
    proper_noun_max_run: int = 4
    # Inputs longer than this are truncated (protects memory / speed).
    max_chars: int = 10_000


DEFAULT_CONFIG = Config()


# --------------------------------------------------------------------------- #
# Result type
# --------------------------------------------------------------------------- #
REASON_DESCRIPTIONS = {
    "SCRIPT": "Contains letters from a non-Latin alphabet (e.g. Chinese, Russian, Arabic).",
    "SEGMENT": "Contains a phrase or sentence the language detector judged not English.",
    "TOKEN": "Contains individual words that are far more common in another language.",
    "ERROR": "Processing failed; flagged so a person reviews it.",
}


@dataclass
class Result:
    """What check_feedback() returns for one comment."""

    # True if the comment contains anything other than English.
    flagged: bool
    # Non-English languages found, e.g. ["SPANISH"] or ["FRENCH", "GERMAN"].
    languages: list[str] = field(default_factory=list)
    # Which check fired: SCRIPT (non-Latin letters), SEGMENT (foreign phrase),
    # TOKEN (foreign word), ERROR (processing failed -> flagged for review).
    reasons: list[str] = field(default_factory=list)
    # The exact words / phrases that triggered the flag (max 10).
    evidence: list[str] = field(default_factory=list)
    # Error message if processing failed, else None.
    error: str | None = None

    def to_dict(self) -> dict:
        """JSON-serializable dict (what an API should return)."""
        return {
            "flagged": self.flagged,
            "languages": self.languages,
            "reasons": self.reasons,
            "reason_descriptions": [REASON_DESCRIPTIONS[r] for r in self.reasons],
            "evidence": self.evidence,
            "error": self.error,
        }

    def to_json(self, **kwargs) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, **kwargs)


# --------------------------------------------------------------------------- #
# Internals
# --------------------------------------------------------------------------- #
_NOISE = re.compile(
    r"https?://\S+|www\.\S+|\S+@\S+|[@#]\w+|\d+|[^\w\s'’-]|_",
    flags=re.UNICODE,
)
_WS = re.compile(r"\s+")
_LATIN_EXTRA = re.compile(r"[ªº]")


@lru_cache(maxsize=1)
def _detector() -> LanguageDetector:
    """Build once per process (loads ~75 language models, a few hundred MB)."""
    log.info("Loading language models...")
    return (
        LanguageDetectorBuilder.from_all_languages()
        .with_preloaded_language_models()
        .build()
    )


def warmup() -> None:
    """Load all models now. Call once at service startup (before readiness).

    Without this the models load lazily on the first check_feedback() call,
    making that first request slow.
    """
    _detector()
    check_feedback("warmup")  # also touches the word-frequency data


def _allowed(cfg: Config) -> Language:
    return Language.from_iso_code_639_1(getattr(IsoCode639_1, cfg.allowed_language.upper()))


_WORD = re.compile(r"[^\W\d_][\w'’-]*", re.UNICODE)
_SENTENCE_END = ".!?¿¡:;\n"


def _strip_proper_nouns(raw: str, cfg: Config) -> str:
    """Blank out capitalized mid-sentence words (e.g. 'Thanks, José García').

    Skipped for sentence-initial words, ALL-CAPS words, and long runs of
    capitalized words (title-case text), so Spanish text can't hide as "names".
    """
    if not cfg.ignore_proper_nouns:
        return raw
    cands = []  # (match, is_candidate)
    for m in _WORD.finditer(raw):
        w = m.group()
        prefix = raw[: m.start()]
        head = prefix.rstrip()
        initial = (not head) or head[-1] in _SENTENCE_END or "\n" in prefix[len(head):]
        cands.append((m, w[0].isupper() and not w.isupper() and not initial))
    out, last, i = [], 0, 0
    while i < len(cands):
        if not cands[i][1]:
            i += 1
            continue
        j = i
        while j < len(cands) and cands[j][1]:
            j += 1
        if j - i <= cfg.proper_noun_max_run:
            for m, _ in cands[i:j]:
                out.append(raw[last : m.start()])
                last = m.end()
        i = j
    out.append(raw[last:])
    return " ".join(out) if len(out) > 1 else raw


def _clean(text: str) -> str:
    return _WS.sub(" ", _NOISE.sub(" ", text)).strip()


def _non_latin_letters(text: str) -> str:
    """Return the non-Latin letters found in `text` (empty string if none)."""
    found = []
    for ch in text:
        if ch.isalpha() and not _LATIN_EXTRA.match(ch):
            if not unicodedata.name(ch, "").startswith("LATIN"):
                found.append(ch)
    return "".join(found)


def _script_check(text: str) -> tuple[list[str], list[str]]:
    chars = _non_latin_letters(text)
    if not chars:
        return [], []
    return ["NON_LATIN_SCRIPT"], [chars[:40]]


def _segment_check(cleaned: str, cfg: Config) -> tuple[list[str], list[str]]:
    det = _detector()
    allowed = _allowed(cfg)
    langs: list[str] = []
    evidence: list[str] = []
    for seg in det.detect_multiple_languages_of(cleaned):
        if seg.language == allowed:
            continue
        chunk = cleaned[seg.start_index : seg.end_index].strip()
        words = [w for w in chunk.lower().split() if w not in cfg.allowlist]
        if len(words) < cfg.segment_min_words:
            continue
        # Re-score the segment on its own; ignore weak / allowed-language-leaning ones.
        conf = det.compute_language_confidence_values(chunk)
        if not conf:
            continue
        top = conf[0]
        if top.language == allowed or top.value < cfg.segment_min_confidence:
            continue
        langs.append(top.language.name)
        evidence.append(chunk)
    return langs, evidence


def _token_check(cleaned: str, cfg: Config) -> tuple[list[str], list[str]]:
    langs: list[str] = []
    evidence: list[str] = []
    for tok in cleaned.lower().split():
        tok = tok.strip("'’-")
        if len(tok) < cfg.token_min_len or tok in cfg.allowlist:
            continue
        ok = zipf_frequency(tok, cfg.allowed_language)
        best_lang, best = None, 0.0
        for lang in cfg.token_languages:
            if lang == cfg.allowed_language:
                continue
            z = zipf_frequency(tok, lang)
            if z > best:
                best_lang, best = lang, z
        if best_lang and best >= cfg.token_min_zipf and best - ok >= cfg.token_margin:
            langs.append(best_lang.upper())
            evidence.append(tok)
    if len(evidence) < cfg.token_min_hits:
        return [], []
    return langs, evidence


_TOKEN_LANG_NAMES = {
    "EN": "ENGLISH", "ES": "SPANISH", "PT": "PORTUGUESE", "FR": "FRENCH", "IT": "ITALIAN",
    "DE": "GERMAN", "CA": "CATALAN", "NL": "DUTCH",
}


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def check_feedback(text: object, cfg: Config = DEFAULT_CONFIG) -> Result:
    """Return a Result; flagged=True if any part of `text` is not English.

    Never raises: unexpected failures are returned as flagged=True with
    `error` set, so a bad record is routed to human review, not silently passed.
    """
    try:
        if text is None or (isinstance(text, float) and text != text):  # None/NaN
            return Result(False)
        raw = unicodedata.normalize("NFKC", str(text))[: cfg.max_chars]
        if not raw.strip():
            return Result(False)

        languages: list[str] = []
        reasons: list[str] = []
        evidence: list[str] = []
        analysed = _clean(_strip_proper_nouns(raw, cfg))

        for name, (l, e) in (
            ("SCRIPT", _script_check(raw)),
            ("SEGMENT", _segment_check(analysed, cfg)),
            ("TOKEN", _token_check(analysed, cfg)),
        ):
            if e:
                reasons.append(name)
                languages += [_TOKEN_LANG_NAMES.get(x, x) for x in l]
                evidence += e

        return Result(
            flagged=bool(reasons),
            languages=sorted(set(languages)),
            reasons=reasons,
            evidence=evidence[:10],
        )
    except Exception as exc:  # noqa: BLE001 - fail closed, see docstring
        log.exception("Detection failed")
        return Result(True, reasons=["ERROR"], error=f"{type(exc).__name__}: {exc}")


def flag_feedback(
    items: Iterable[object], cfg: Config = DEFAULT_CONFIG
) -> Iterator[tuple[int, Result]]:
    """Yield (index, Result) for every item (flagged or not)."""
    for i, text in enumerate(items):
        yield i, check_feedback(text, cfg)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _read_rows(path: str, fmt: str) -> Iterator[dict]:
    fh = sys.stdin if path == "-" else open(path, newline="", encoding="utf-8-sig")
    with fh:
        if fmt == "csv":
            yield from csv.DictReader(fh)
        elif fmt == "jsonl":
            for n, line in enumerate(fh, 1):
                if line.strip():
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        log.warning("Skipping invalid JSON on line %d", n)
        else:  # plain text, one comment per line
            for line in fh:
                yield {"text": line.rstrip("\n")}


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("input", help="input file, or '-' for stdin")
    ap.add_argument("--format", choices=["csv", "jsonl", "txt"], default=None)
    ap.add_argument("--column", default="text", help="field holding the feedback")
    ap.add_argument("-o", "--output", default="-", help="output CSV (default stdout)")
    ap.add_argument("--all", action="store_true", help="write all rows, not only flagged")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )
    fmt = args.format or (
        "jsonl" if args.input.endswith((".jsonl", ".ndjson"))
        else "txt" if args.input == "-" or args.input.endswith(".txt")
        else "csv"
    )

    out = sys.stdout if args.output == "-" else open(args.output, "w", newline="", encoding="utf-8")
    writer: csv.DictWriter | None = None
    total = flagged = 0
    try:
        for row in _read_rows(args.input, fmt):
            total += 1
            res = check_feedback(row.get(args.column))
            flagged += res.flagged
            if not (res.flagged or args.all):
                continue
            record = {
                **row,
                "flagged": res.flagged,
                "languages": ";".join(res.languages),
                "reasons": ";".join(res.reasons),
                "evidence": " | ".join(res.evidence),
                "error": res.error or "",
            }
            if writer is None:
                writer = csv.DictWriter(out, fieldnames=list(record))
                writer.writeheader()
            writer.writerow(record)
    finally:
        if out is not sys.stdout:
            out.close()
    log.warning("Processed %d rows, flagged %d", total, flagged)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
