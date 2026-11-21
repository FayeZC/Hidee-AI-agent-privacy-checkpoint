"""Sensitive facts written as free text ("Jane is being treated for depression"), detected locally.

Three layers, each giving highlighted spans with a category; everything runs in the Hidee process, with no model and
no network call.

a. Sensitive facts by category: a curated lexicon of words and phrase patterns for the special categories of
   personal data (GDPR Art. 9(1): health, including mental health; sex life or sexual orientation; religious or
   philosophical beliefs; political opinions; trade-union membership; racial or ethnic origin; genetic and biometric
   data) and for other facts people commonly treat as sensitive (finances and debt, legal trouble and criminal
   records, immigration status, family and relationship matters). The lexicon was written from general sources:
   the category definitions in GDPR Art. 9 and Recitals 51-54, the PHI definition in 45 CFR 160.103, NIST SP 800-122
   (examples of PII and its context), the chapter headings of ICD-10 and the diagnostic class names of DSM-5 (names
   only), and common plain-language words for each category. It was not built from any benchmark or test data.
b. Restated known data: when the user designates sensitive files, a sentence in an outbound call that restates a
   passage of those files (not only an exact value) is highlighted as `known_fact`. The score is the share of the
   sentence's content words (and word pairs) that also occur in one passage of the files; a sentence counts at or
   above RESTATE_THRESHOLD with at least RESTATE_MIN_SHARED shared content words (the threshold was set on a
   development split of a public benchmark and fixed before evaluation; see docs/RULES.md).
c. Attribution to a person: a lexicon hit counts only when its sentence is about a person (a personal name, a
   pronoun such as she or his, a relation such as "my wife" or "the patient", or a known contact from the designated
   files), or when the phrase itself states a personal fact ("diagnosed with", "was arrested", "filed for
   bankruptcy"). So "depression" in a general article is not highlighted, while "Jane ... depression" is.

The span of a finding is the whole sentence (or line) that carries the fact, so the shorter version removes the fact,
not only the word; consecutive sentences with the same category form one highlight. The evidence log records only the category, never the text.
"""
from __future__ import annotations

import re

from .detect import Finding

# ---------------------------------------------------------------- layer a: lexicon (word stems, matched as words)
W = r"(?:s|es|ed|ing|ly|al|ic|y)?"
TERMS: dict[str, list[str]] = {
    "health": [
        r"diagnos(?:is|es|ed|e)", r"symptoms?", r"prognos(?:is|es)", r"chronic illness", r"terminal(?:ly)? ill",
        r"cancer", r"tumou?rs?", r"leukemia", r"lymphoma", r"chemo(?:therapy)?", r"radiation therapy", r"biops(?:y|ies)",
        r"diabet(?:es|ic)", r"insulin", r"asthma", r"copd", r"hypertension", r"high blood pressure", r"heart attack",
        r"heart (?:disease|condition|failure)", r"(?:had|suffered|a) stroke", r"epilep(?:sy|tic)", r"seizures?", r"migraines?",
        r"multiple sclerosis", r"parkinson'?s", r"alzheimer'?s", r"dementia", r"arthritis", r"lupus", r"crohn'?s",
        r"hiv", r"hepatitis", r"tuberculosis", r"herpes", r"chlamydia", r"syphilis",
        r"surger(?:y|ies)", r"hospitali[sz]ed", r"in (?:the )?hospital", r"icu", r"emergency room",
        r"prescri(?:bed|ption)", r"medications?", r"meds", r"dosage", r"antibiotics?", r"chart note", r"medical record",
        r"disabilit(?:y|ies)", r"wheelchair", r"blindness", r"deafness", r"dyslexi[ac]",
        r"injur(?:y|ies|ed)", r"concussion", r"fracture[sd]?", r"allerg(?:y|ies|ic)", r"infection", r"covid",
        r"physical therapy", r"rehabilitation", r"dialysis", r"transplant", r"blood test", r"test results?",
        r"miscarriage", r"abortion", r"infertil(?:e|ity)", r"ivf", r"sick leave", r"medical leave", r"illness",
    ],
    "mental_health": [
        r"depress(?:ion|ed|ive)", r"anxiety", r"panic attacks?", r"bipolar", r"schizophreni[ac]", r"psychos(?:is|es)",
        r"psychotic", r"ptsd", r"post-traumatic", r"trauma(?:tized)?", r"ocd", r"obsessive-compulsive", r"adhd",
        r"autis(?:m|tic)", r"eating disorder", r"anorexi[ac]", r"bulimi[ac]", r"suicid(?:e|al)", r"self-harm",
        r"self harm", r"mental (?:health|illness|breakdown)", r"nervous breakdown", r"burnout", r"therap(?:y|ist)",
        r"psychiatr(?:y|ist|ic)", r"psycholog(?:ist)", r"counsel(?:l)?ing", r"counsel(?:l)?or", r"antidepressants?",
        r"addict(?:ion|ed)", r"substance abuse", r"alcoholi(?:sm|c)", r"rehab", r"overdos(?:e|ed)", r"relapsed?",
        r"grief counseling",
    ],
    "sexual_orientation": [
        r"gay", r"lesbian", r"bisexual", r"transgender", r"trans (?:woman|man|person)", r"queer", r"lgbtq?\+?",
        r"non-binary", r"nonbinary", r"sexual orientation", r"came out", r"coming out", r"same-sex", r"sex life",
        r"sexuality", r"gender identity", r"transition(?:ing|ed)? (?:gender|to)",
    ],
    "religion": [
        r"religio(?:n|us)", r"muslim", r"islam(?:ic)?", r"christian(?:ity)?", r"catholic", r"protestant",
        r"evangelical", r"mormon", r"jewish", r"judaism", r"hindu(?:ism)?", r"buddhis(?:t|m)", r"sikh", r"atheis(?:t|m)",
        r"agnostic", r"mosque", r"synagogue", r"church(?:goer)?", r"ramadan", r"scientolog(?:y|ist)", r"jehovah'?s witness",
    ],
    "political": [
        r"democrat(?:ic)?", r"republican", r"conservative party", r"labou?r party", r"libertarian", r"socialist",
        r"communist", r"political (?:views?|opinions?|beliefs?|affiliation|party)", r"party member(?:ship)?", ],
    "trade_union": [r"trade union", r"labou?r union", r"union (?:member|membership|rep|representative|dues)",
                    r"unioni[sz]ed", r"on strike", r"collective bargaining", r"shop steward"],
    "ethnic_origin": [r"ethnicit(?:y|ies)", r"ethnic (?:origin|background|group)", r"racial", r"african american", r"hispanic", r"latin[oax]", r"asian american", r"indigenous",
                      r"native american", r"aboriginal", r"caucasian", r"immigrant background"],
    "genetic_biometric": [r"genetic (?:test(?:ing)?|condition|disorder|marker)", r"dna test", r"genome", r"brca[12]?",
                          r"hereditary", r"biometric", r"fingerprints?", r"face scan", r"facial recognition",
                          r"retina scan", r"iris scan", r"voiceprint"],
    "finances": [r"debts?", r"in debt", r"bankrupt(?:cy)?", r"foreclos(?:ure|ed)", r"evict(?:ion|ed)",
                 r"collections agency", r"debt collector", r"defaulted", r"loan default", r"credit score",
                 r"unpaid (?:bills?|rent)", r"behind on (?:rent|payments|bills)", r"net worth", r"payday loan", r"garnish(?:ed|ment)", r"welfare", r"food stamps",
                 ],
    "legal": [r"arrest(?:ed)?", r"criminal record", r"convict(?:ed|ion)", r"felon(?:y|ies)?", r"misdemeanou?r",
              r"jail(?:ed)?", r"prison", r"incarcerat(?:ed|ion)", r"probation", r"parole", r"dui", r"dwi",
              r"charged with", r"indict(?:ed|ment)", r"lawsuit", r"sued", r"suing", r"litigation", r"court (?:date|case|hearing)",
              r"restraining order", r"accused of", r"allegation", r"embezzl(?:e|ed|ement|ing)", r"fraud", r"theft",
              r"assault(?:ed)?", r"plea (?:deal|bargain)", r"pleaded guilty", r"defendant", r"under investigation",
              r"police report"],
    "immigration": [r"undocumented", r"illegal(?:ly)? (?:immigrant|alien)", r"immigration status", r"visa status",
                    r"overstay(?:ed)?", r"green card", r"work permit", r"deport(?:ed|ation)", r"asylum", r"refugee",
                    r"daca", r"citizenship (?:application|status)", r"residence permit"],
    "family": [r"divorc(?:e|ed|ing)", r"custody", r"child support", r"alimony", r"pregnan(?:t|cy)",
               r"expecting a baby", r"cheat(?:ed|ing) on", r"domestic (?:violence|abuse)", r"abus(?:e|ed|ive)",
               r"restraining order", r"foster (?:care|child)", r"estranged", r"widow(?:ed|er)?",
               r"break-?up", r"broke up", r"family (?:crisis|problems|issues)", r"marital problems"],
}
# Phrases that state a personal fact on their own (no separate person reference needed).
STRONG = {
    "health": [r"diagnosed with", r"(?:is|was|being|been) treated for", r"suffer(?:s|ed|ing)? from", r"recovering from",
               r"(?:takes|taking|on) (?:medication|meds|insulin)", r"(?:had|having|needs?) (?:surgery|an operation)",
               r"tested positive"],
    "mental_health": [r"(?:sees|seeing) a (?:therapist|psychiatrist|counsel(?:l)?or)", r"(?:in|went to|checked into) rehab",
                      r"attempted suicide"],
    "sexual_orientation": [r"came out as", r"(?:is|am) (?:gay|lesbian|bisexual|transgender|queer)"],
    "religion": [r"converted to"],
    "legal": [r"(?:was|got|been|were) arrested", r"criminal record", r"(?:was|been) convicted", r"charged with",
              r"accused of", r"on probation", r"served time"],
    "finances": [r"filed for bankruptcy", r"(?:is|are|was) in debt", r"behind on (?:rent|payments|bills)",
                 r"(?:lost|losing) (?:his|her|their|my) job"],
    "immigration": [r"(?:is|are|was) undocumented", r"facing deportation", r"applied for asylum"],
    "family": [r"filed for divorce", r"having an affair", r"(?:is|are|were) separated", r"(?:is|was) pregnant", r"getting divorced", r"custody battle"],
}
LABELS = {"mental_health": "mental health", "sexual_orientation": "sexual orientation or sex life",
          "religion": "religion or beliefs", "political": "political opinions", "trade_union": "trade-union membership",
          "ethnic_origin": "racial or ethnic origin", "genetic_biometric": "genetic or biometric data",
          "finances": "finances or debt", "legal": "legal trouble", "immigration": "immigration status",
          "family": "family or relationship matters", "known_fact": "restates your sensitive files"}
CATEGORIES = tuple(LABELS)
# When a sentence matches several categories, the more specific one names it.
PRIORITY = ("mental_health", "sexual_orientation", "genetic_biometric", "health", "religion", "political", "trade_union",
            "ethnic_origin", "immigration", "legal", "finances", "family")          # "health" already exists in detect.LABELS

_COMPILED = {c: re.compile(r"(?<![\w-])(?:" + "|".join(t for t in terms) + r")(?![\w-])", re.I) for c, terms in TERMS.items()}
_STRONG = {c: re.compile(r"(?<![\w-])(?:" + "|".join(p) + r")(?![\w-])", re.I) for c, p in STRONG.items()}

# ---------------------------------------------------------------- layer c: is the sentence about a person?
PRONOUN = re.compile(r"\b(?:he|she|him|his|her|hers|they|them|their|i|me|my|we|our|you|your)\b", re.I)
RELATION = re.compile(r"\b(?:wife|husband|partner|spouse|son|daughter|child|kid|mother|mom|mum|father|dad|brother|sister|"
                      r"aunt|uncle|cousin|grandma|grandpa|grandmother|grandfather|friend|boyfriend|girlfriend|fianc[eé]e?|"
                      r"colleague|coworker|co-worker|boss|manager|employee|staff member|client|customer|patient|student|"
                      r"pupil|tenant|neighbou?r|roommate|applicant|member|parent|guardian|caregiver)s?\b", re.I)
NAME = re.compile(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?\b")
NOT_NAMES = {"the", "a", "an", "this", "that", "these", "those", "it", "i", "we", "you", "he", "she", "they", "please",
             "hi", "hello", "dear", "thanks", "thank", "regards", "best", "monday", "tuesday", "wednesday", "thursday",
             "friday", "saturday", "sunday", "january", "february", "march", "april", "may", "june", "july", "august",
             "september", "october", "november", "december", "today", "tomorrow", "yesterday", "here", "there", "our",
             "my", "your", "his", "her", "their", "if", "when", "as", "for", "and", "but", "or", "so", "also", "note",
             "subject", "re", "fwd", "update", "team", "all", "everyone", "good", "great"}


def about_person(sentence: str, contacts: list[str] = ()) -> bool:
    low = sentence.lower()
    if any(c and c.lower() in low for c in contacts):
        return True
    if PRONOUN.search(sentence) or RELATION.search(sentence):
        return True
    for m in NAME.finditer(sentence):
        first = m.group().split()[0]
        if first.lower() in NOT_NAMES:
            continue
        if m.start() == len(sentence) - len(sentence.lstrip()) and " " not in m.group():
            # a single capitalized word that starts the sentence is a name only if it is not a lexicon word
            if any(p.fullmatch(first) for p in _COMPILED.values()):
                continue
        return True
    return False


# ---------------------------------------------------------------- sentences
SENTENCE = re.compile(r"[^.!?\n]+(?:[.!?]+|\n|$)")


def sentences(text: str):
    """(start, end) of each sentence or line, without surrounding spaces."""
    for m in SENTENCE.finditer(text):
        s, e = m.start(), m.end()
        while s < e and text[s] in " \t\r\n\"'*-•>":
            s += 1
        while e > s and text[e - 1] in " \t\r\n":
            e -= 1
        if e - s >= 3:
            yield s, e


# ---------------------------------------------------------------- layer b: restated known data
STOP = set("""a an the and or but if then than so of to in on at by for with from into onto over under about as is are was
were be been being am do does did doing have has had having it its this that these those there here which who whom whose
what when where why how all any both each few more most other some such no nor not only own same too very can will just
should could would may might must shall i me my we our you your he him his she her they them their also please thanks
thank hi hello dear regards best""".split())
RESTATE_THRESHOLD = 0.2
RESTATE_MIN_SHARED = 3


def _stem(w: str) -> str:
    for suf in ("ing", "edly", "ed", "es", "s", "ly"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def content_words(text: str) -> list[str]:
    return [_stem(w) for w in re.findall(r"[a-z0-9']+", text.lower()) if w not in STOP and len(w) > 2]


class RestateIndex:
    """Passages of the designated files, indexed by content word, for finding restatements."""

    def __init__(self, passages: list[str]) -> None:
        self.passages = []
        self.index: dict[str, set[int]] = {}
        for p in passages:
            words = content_words(p)
            if len(words) < RESTATE_MIN_SHARED:
                continue
            i = len(self.passages)
            self.passages.append((set(words), set(zip(words, words[1:]))))
            for w in set(words):
                self.index.setdefault(w, set()).add(i)

    def score(self, sentence: str) -> tuple[float, int]:
        words = content_words(sentence)
        if len(words) < RESTATE_MIN_SHARED:
            return 0.0, 0
        ws, bs = set(words), set(zip(words, words[1:]))
        cand: dict[int, int] = {}
        for w in ws:
            for i in self.index.get(w, ()):
                cand[i] = cand.get(i, 0) + 1
        best, shared = 0.0, 0
        for i, n in sorted(cand.items(), key=lambda kv: -kv[1])[:20]:
            pw, pb = self.passages[i]
            uni = len(ws & pw) / len(ws)
            bi = len(bs & pb) / len(bs) if bs else 0.0
            s = 0.6 * uni + 0.4 * bi
            if s > best:
                best, shared = s, len(ws & pw)
        return best, shared


# ---------------------------------------------------------------- the detector
def find(text: str, known=None) -> list[Finding]:
    """Free-text findings in `text`: one per sentence at most, the sentence as the span, with the first category that
    applies (a known-data restatement first, then the lexicon in TERMS order)."""
    out = []
    contacts = getattr(known, "names", []) if known is not None else []
    index = getattr(known, "restate", None) if known is not None else None
    for s, e in sentences(text):
        sent = text[s:e]
        cat = None
        if index is not None:
            # exact known values are found on their own; a restatement is what the sentence repeats beyond them
            masked = sent
            for f in sorted(known.find(sent), key=lambda f: -f.start):
                masked = masked[:f.start] + " " + masked[f.end:]
            score, shared = index.score(masked)
            if score >= RESTATE_THRESHOLD and shared >= RESTATE_MIN_SHARED:
                cat = "known_fact"
        if cat is None:
            hits = [c for c in PRIORITY if _COMPILED[c].search(sent)]
            strong = next((c for c in PRIORITY if c in _STRONG and _STRONG[c].search(sent)), None)
            if hits and (strong or about_person(sent, contacts)):
                cat = hits[0]
            elif strong:
                cat = strong
        if cat:
            prev = out[-1] if out else None
            if prev is not None and prev.category == cat and not text[prev.end:s].strip():
                # consecutive sentences with the same category form one highlight (one passage on the card)
                out[-1] = Finding(cat, prev.start, e, text[prev.start:e])
            else:
                out.append(Finding(cat, s, e, sent))
    return out
