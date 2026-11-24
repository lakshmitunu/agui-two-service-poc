# Non-English Feedback Flagger

Flags customer feedback that contains **any language other than English**.
Mixed text (for example English + Spanish in one message) is flagged too.
English is the only allowed language (`Config.allowed_language = "en"`).

| Comment | Result |
|---|---|
| `The product arrived broken` | not flagged |
| `El producto llegó roto` | flagged (Spanish) |
| `Great service, pero la entrega fue lenta` | flagged (mixed) |
| `Le produit est cassé` | flagged (French) |
| `Great service 非常好` | flagged (non-Latin script) |
| `Thanks, José García` | not flagged (names are ignored) |

## Files

| File | Purpose |
|---|---|
| `flag_non_english.py` | The module, plus a command-line tool |
| `test_flag_non_english.py` | Tests (29) |
| `requirements.txt` | Pinned runtime dependencies |
| `requirements-dev.txt` | Runtime + test dependencies |
| `EKS_INTEGRATION_PROMPT.txt` | Prompt to give Claude to build the EKS service |

## Dependencies (pinned)

```
lingua-language-detector==2.2.0
wordfreq==3.1.1
```

Tested with Python 3.14. `wordfreq` also installs `ftfy`, `langcodes`, `locate`,
`msgpack` and `regex`. `lingua` has no extra dependencies. Both libraries ship
their own data: no downloads, no API keys, no network calls at runtime.
`pytest==9.2.0` is needed only to run tests.

**Keep versions pinned.** An update to either library can change which comments
get flagged. Upgrade deliberately, then rerun the tests and your labeled sample.

```bash
pip install -r requirements.txt        # runtime
pip install -r requirements-dev.txt    # runtime + tests
python -m pytest -q test_flag_non_english.py
```

## How it works

Three checks run on every comment. **Any one of them flags the comment.**

1. **SCRIPT** - letters from a non-Latin alphabet (Chinese, Russian, Arabic...).
2. **SEGMENT** - `lingua` splits the text into one-language stretches. A stretch
   of 3+ words that is confidently not English is flagged
   ("Great service, pero la entrega fue lenta").
3. **TOKEN** - each word is compared with the `wordfreq` word lists. A word that
   is much more common in another language than in English is flagged. This
   catches a single foreign word inside an English sentence.

Before the checks, the text is cleaned (URLs, emails, numbers, emojis and
punctuation removed), allowlisted words are skipped, and capitalized
mid-sentence words (likely names) are ignored.

### What the libraries do vs. what this code does

- **Libraries:** `lingua` recognizes ~75 languages and scores phrases.
  `wordfreq` only returns how common a word is in a language.
- **This code:** cleaning, the script check, segment filtering, the word
  comparison rule (foreign score minus English score), name handling, combining
  the checks, the fail-safe, JSON output, the CLI, and all thresholds.

Neither library solves the problem alone: `lingua` misses a lone foreign word in
an English sentence, and `wordfreq` can't judge sentences and its word lists
contain some wrong-language words (that is why the rule compares scores).

## Usage

### Python

```python
from flag_non_english import check_feedback

result = check_feedback("Great service, pero la entrega fue muy lenta")
result.flagged          # True
result.to_dict()        # JSON-serializable dict (return this from an API)
result.to_json()        # JSON string
```

### Return value (`Result`)

```json
{
  "flagged": true,
  "languages": ["PORTUGUESE", "SPANISH"],
  "reasons": ["TOKEN"],
  "reason_descriptions": ["Contains individual words that are far more common in another language."],
  "evidence": ["pero", "entrega", "fue", "muy", "lenta"],
  "error": null
}
```

| Field | Meaning |
|---|---|
| `flagged` | `true` if anything other than English was found. **Use this for the decision.** |
| `languages` | Languages found. Only a hint: Spanish and Portuguese share many words, so a word check can label a Spanish word as Portuguese. |
| `reasons` | Which check fired (list, because several can fire) |
| `reason_descriptions` | Plain-language description of each reason |
| `evidence` | The exact words/phrases that caused the flag (max 10) |
| `error` | Error message if processing failed, otherwise `null` |

Reason codes:

| Code | Meaning |
|---|---|
| `SCRIPT` | Non-Latin letters (Chinese, Russian, Arabic...) |
| `SEGMENT` | A phrase/sentence judged not English |
| `TOKEN` | Individual words far more common in another language |
| `ERROR` | Processing failed, so the comment is flagged for human review |

Empty, `None` and NaN input is not flagged. Any unexpected error is **flagged**
(`ERROR`), never silently passed. `check_feedback` does not raise.

### Command line

```bash
python flag_non_english.py feedback.csv --column comment -o flagged.csv
python flag_non_english.py feedback.jsonl --column text --format jsonl
cat comments.txt | python flag_non_english.py -         # one comment per line
python flag_non_english.py feedback.csv --column comment --all   # keep all rows
```

Output columns added: `flagged, languages, reasons, evidence, error`.

## Startup loading (important for EKS)

The language models load **once per process**, lazily on the first
`check_feedback()` call. Without action, the first request is slow. Call
`warmup()` at service startup, before the pod reports Ready:

```python
import flag_non_english as f

f.warmup()                      # load models now (~0.5 s locally)
f.check_feedback("some text")   # fast from here on (~0.1 s first, then ms)
```

- Each new process loads its own copy. With several workers (gunicorn), call
  `warmup()` before the workers fork and use `--preload` so memory is shared, or
  run one worker per pod and scale with pods.
- Do not create a new process per comment.

## Resources (measured on a laptop, not on EKS)

| | Result |
|---|---|
| Memory after warmup | ~300 MB |
| Memory after 5,000 comments | ~400 MB (flat; does not grow with volume) |
| Speed | ~390 comments/second on one CPU (5,000 in ~13 s) |

Suggested pod settings: memory request `768Mi`, limit `1Gi` (never below
`512Mi`, or the pod can be OOM-killed); CPU request `250m`, limit `1`; one
replica is enough for ~100 comments/day (two for availability). For a 5,000
batch test, accept the request and return a job id or stream results so the HTTP
call does not time out. Confirm real numbers with `kubectl top pod`.

## Settings (`Config`)

All thresholds live in the `Config` dataclass in `flag_non_english.py`, each
with a description in the code. Rule of thumb: **English comments wrongly
flagged -> make the setting stricter (raise); foreign text slipped through ->
make it looser (lower).** Tune one setting at a time on a few hundred labeled
real comments.

| Setting | Default | Raise it when | Lower it when |
|---|---|---|---|
| `allowed_language` | `en` | - | - (change only if the allowed language changes) |
| `segment_min_words` | 3 | short English phrases flagged | short foreign phrases missed |
| `segment_min_confidence` | 0.60 | English comments flagged | foreign phrases missed |
| `token_min_len` | 3 | short English words flagged | tiny foreign words missed |
| `token_min_zipf` | 3.5 | rare English words / typos flagged | uncommon foreign words missed |
| `token_margin` | 1.2 | English words also used abroad flagged | foreign words also known in English missed |
| `token_min_hits` | 1 | one stray word causes too many flags | keep at 1 to flag even one foreign word |
| `token_languages` | es, pt, fr, it, de, ca, nl | remove a noisy language | add a language you receive |
| `allowlist` | brands, loanwords | add words that are wrongly flagged | - |
| `ignore_proper_nouns` | `True` | - | turn off if Title Case foreign text is missed |
| `proper_noun_max_run` | 4 | long names flagged | Title Case foreign phrases hide as names |
| `max_chars` | 10000 | - | - |

### How to tune

1. Collect a few hundred real comments and label each: English or not.
2. Run with `--all` and compare the flags with your labels.
3. Look at both errors: English flagged wrongly, and foreign missed.
4. The `reasons` and `evidence` columns show which check caused each wrong
   flag. A wrong `TOKEN` flag points to the word settings; a wrong `SEGMENT`
   flag points to the phrase settings.
5. Change one setting, rerun, compare. Add recurring false flags (brand and
   product names) to `allowlist` first.

## Known limitations

- **Not yet measured on real data.** Thresholds are defaults tested on
  hand-written examples. Measure and tune before relying on it.
- Very short comments (1-2 words) are the least reliable.
- A lone foreign word is only caught for the 7 languages in `token_languages`.
- A name at the very start of a comment, or a name typed in lowercase, can be
  flagged. Add recurring names to `allowlist`.
- Short Spanish text in Title Case (`Muy Buen Servicio`) can be mistaken for
  names (runs longer than `proper_noun_max_run` are still checked).
- Languages written in Latin script that `lingua` does not cover are not
  detected as phrases.

## Production checklist (not yet done)

- [ ] Measure accuracy on real labeled comments and tune thresholds.
- [ ] API wrapper (e.g. FastAPI) with `/health` and `/ready`, calling `warmup()` at startup.
- [ ] Dockerfile and Kubernetes manifests (see `EKS_INTEGRATION_PROMPT.txt`).
- [ ] Verify behavior and memory inside the container on EKS.
- [ ] Review logging so customer text is not written to logs.
- [ ] Metrics (flag rate, latency, error count) and CI running tests, lint, type checks.
