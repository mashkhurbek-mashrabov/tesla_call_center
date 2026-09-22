"""Layer 2 of the topic guard: a server-side check on anything heading into the KB.

Layer 1 is the system instruction in config.py. This layer exists because a system
instruction can be talked around; a code check cannot. It runs on the tool-call query
and on the caller's transcribed turn, so it must be cheap -- no LLM round trip inside
a voice turn.

# ponytail: keyword matching, not a classifier model. A trained classifier is the
# upgrade path if false rejects show up in real call logs.
"""

import re

# Domain vocabulary. A hit here means the text is plausibly Tesla support.
ON_TOPIC = {
    "tesla", "model 3", "model y", "model s", "model x", "cybertruck", "roadster",
    "semi", "powerwall", "megapack", "solar roof", "wall connector", "mobile connector",
    "supercharger", "supercharging", "supercharge", "charging", "charger", "charge",
    "kwh", "kilowatt", "battery", "range", "miles per hour", "nacs", "plug", "adapter",
    "warranty", "coverage", "covered", "extended service", "esa", "claim",
    "service", "appointment", "service center", "mobile service", "technician",
    "repair", "estimate", "collision", "body shop", "roadside", "tow", "towing",
    "lockout", "locked out", "flat tire", "tire", "tires", "wheel", "alignment",
    "rotation", "brake", "wiper",
    "order", "ordered", "delivery", "deliver", "vin", "trade-in", "trade in",
    "financing", "lease", "leasing", "configurator", "design studio", "pickup",
    "app", "tesla app", "account", "phone key", "key card", "key fob", "login",
    "sign in", "password", "two-factor", "subscription", "premium connectivity",
    "software", "update", "firmware", "release notes", "touchscreen", "autopilot",
    "full self-driving", "fsd", "summon", "sentry mode", "dog mode", "camp mode",
    "idle fee", "congestion fee", "invoice", "billing", "bill", "payment", "refund",
    "energy", "solar", "backup", "outage", "grid", "inverter",
    "vehicle", "car", "truck", "drive unit", "motor", "frunk", "trunk",
    "support", "help", "customer service", "representative", "agent",
    # --- Uzbek. The agent speaks Uzbek, so real callers arrive in Uzbek. ---
    # vehicle / parts
    "mashina", "avtomobil", "avto", "transport", "gildirak", "shina", "dvigatel",
    "akkumulyator", "batareya", "quvvat", "zaryad", "zaryadlash", "quvvatlash",
    # charging / energy
    "quvvatlagich", "rozetka", "elektr", "energiya", "quyosh", "panel",
    # warranty / service
    "kafolat", "kafolatli", "ta'mir", "ta'mirlash", "xizmat", "xizmati", "servis",
    "usta", "texnik", "ko'rik", "navbat", "uchrashuv", "band qilish",
    # orders / money
    "buyurtma", "buyurtma berish", "yetkazib berish", "yetkazish", "narx", "narxi",
    "to'lov", "to'lash", "hisob", "chek", "pul", "qaytarish", "kredit", "lizing",
    # app / account
    "ilova", "dastur", "hisobim", "akkaunt", "parol", "kirish", "kalit",
    "yangilanish", "yangilash",
    # support words
    "yordam", "yordam bering", "muammo", "savol", "so'ramoqchi", "kerak",
    "qanday", "qancha", "necha", "qayerda", "mumkinmi",
    # greetings / call flow
    "assalomu", "alaykum", "salom", "rahmat", "xayr", "kechirasiz", "marhamat",
    "eshitayapsizmi", "eshityapsizmi", "takrorlang", "bir soniya", "kuting",
}

# Clear off-topic intent. A hit here rejects even if an on-topic word also appears,
# because "write a python script about Tesla" is still a coding request.
OFF_TOPIC = {
    "write a script", "write code", "write me code", "write a program", "write a function",
    "python", "javascript", "java", "sql", "regex", "debug", "compile", "algorithm",
    "leetcode", "homework", "essay", "poem", "haiku", "joke", "riddle", "story",
    "recipe", "cook", "bake", "ingredient", "restaurant", "pizza",
    "weather", "forecast", "sports", "football", "soccer", "basketball", "score",
    "election", "president", "politics", "political", "war", "religion",
    "stock price", "stock", "share price", "invest", "investment", "buy shares",
    "portfolio", "crypto", "bitcoin", "market cap", "earnings call", "ipo",
    "medical", "diagnose", "symptom", "medication", "prescription", "doctor",
    "legal advice", "lawsuit", "sue", "attorney", "contract review",
    "ford", "chevy", "chevrolet", "rivian", "lucid", "bmw", "mercedes", "toyota",
    "honda", "nissan", "hyundai", "kia", "volkswagen", "porsche", "byd",
    "translate", "math problem", "capital of", "who won", "meaning of life",
    # --- Uzbek. Checked BEFORE the on-topic set, so broad Uzbek words like
    # "kerak" or "qanday" cannot rescue an off-topic ask. ---
    "kod yoz", "dastur yoz", "skript", "kod yozib", "python kodi",
    "retsept", "ovqat", "pishir", "taom", "osh",
    "she'r", "hikoya", "latifa", "askiya", "ertak",
    "ob-havo", "sport", "futbol", "chempionat", "o'yin natijasi",
    "siyosat", "prezident", "saylov", "urush", "din",
    "aksiya", "birja", "investitsiya", "sarmoya", "kripto", "bitkoin",
    "shifokor", "kasallik", "dori", "tashxis", "davolash",
    "advokat", "sud", "da'vo",
    "uy vazifasi", "matematika", "masala yech", "insho",
}

# Uzbek off-topic stems that must also match their inflected forms ("aksiya" ->
# "aksiyalarini"). English terms are excluded on purpose -- see _compile_terms.
UZBEK_STEMS = {
    "retsept", "ovqat", "pishir", "taom", "osh",
    "she'r", "hikoya", "latifa", "askiya", "ertak",
    "ob-havo", "sport", "futbol", "chempionat",
    "siyosat", "prezident", "saylov", "urush", "din",
    "aksiya", "birja", "investitsiya", "sarmoya", "kripto", "bitkoin",
    "shifokor", "kasallik", "dori", "tashxis", "davolash",
    "advokat", "sud", "da'vo",
    "matematika", "insho", "skript",
}

# Attempts to rewrite the agent's rules. Rejected outright.
INJECTION = [
    r"ignore (all |your |the )?(previous|prior|above|earlier)",
    r"disregard (all |your |the )?(previous|prior|above|instructions)",
    r"forget (your|all|the) (instructions|rules|prompt|persona)",
    r"you are (now|actually|really) (a|an|not)",
    r"pretend (to be|you are|that you)",
    r"act as (a|an|if)",
    r"(system|initial|original) prompt",
    r"(reveal|show|print|repeat|tell me) (your|the) (prompt|instructions|rules|system)",
    r"new (instructions|rules|persona|role)",
    r"developer mode",
    r"jailbreak",
    r"without (any )?(restrictions|filters|rules)",
    # Uzbek equivalents.
    r"(ko'rsatmalar|qoidalar|buyruqlar)(ni|ingni)? (unut|e'tiborsiz|tashla)",
    r"oldingi (ko'rsatma|qoida|buyruq)",
    r"endi sen (bir |a )?\w+",
    r"o'zingni \w+ deb",
    r"\w+ bo'lib ko'rsat",
    r"(tizim|sistema) (prompt|ko'rsatma)",
    r"(prompt|ko'rsatmalar)ingni (ayt|ko'rsat|yoz)",
    # Language-switch attempts: the Uzbek-only rule is a requirement, not a preference.
    r"(speak|answer|reply|talk|say it)\b[\w ]{0,15}\b(english|russian|russ?ki)",
    r"(ingliz|rus)(cha)? (tilida|tilda) (gapir|javob|ayt)",
    r"switch to (english|russian)",
]

# Pure conversational turns. Allowed without any domain word -- refusing a greeting
# would make the agent feel broken.
CHITCHAT = {
    "hello", "hi", "hey", "good morning", "good afternoon", "good evening",
    "thanks", "thank you", "thank", "appreciate", "great", "perfect", "okay", "ok",
    "yes", "yeah", "no", "nope", "sure", "please", "sorry", "excuse me",
    "bye", "goodbye", "have a good", "take care", "that's all", "that is all",
    "can you hear me", "hear me", "are you there", "hold on", "one sec", "one second",
    "wait", "repeat", "say that again", "what was that", "louder", "speak up",
    "who am i speaking", "who is this", "what's your name", "your name",
    "how are you", "nice to meet",
    # --- Uzbek ---
    "assalomu alaykum", "vaalaykum", "salom", "xayrli tong", "xayrli kun",
    "rahmat", "katta rahmat", "tashakkur", "zo'r", "yaxshi", "ha", "yo'q",
    "mayli", "albatta", "iltimos", "kechirasiz", "uzr",
    "xayr", "ko'rishguncha", "hammasi shu", "bo'ldi",
    "ismingiz nima", "kim bilan gaplashyapman", "yaxshimisiz",
}

_INJECTION_RE = [re.compile(p, re.IGNORECASE) for p in INJECTION]

# Uzbek Latin uses several characters for the same apostrophe (o'zbek / o'zbek /
# oʻzbek). Speech transcription picks whichever it likes, so fold them all to one
# before matching.
_APOSTROPHES = "‘’ʻʼ´`"
_APOSTROPHE_RE = re.compile(f"[{_APOSTROPHES}]")


def normalize(text: str) -> str:
    return _APOSTROPHE_RE.sub("'", text).lower().strip()


def _compile_terms(terms: set[str], suffixes: bool = False) -> list[tuple[str, re.Pattern]]:
    """Word-boundary match each term, so 'war' does not fire inside 'warranty'.

    \\b only works next to word characters, and Uzbek terms can end on an apostrophe
    ("yo'q"), so each edge gets a boundary only where one is meaningful.

    Uzbek is agglutinative: "aksiya" shows up as "aksiyalarini", "to'lov" as
    "to'lovingiz". With suffixes=True, terms listed in UZBEK_STEMS also match their
    inflected forms. This is deliberately NOT applied to English terms -- "war" plus a
    suffix tail would swallow "warranty" and reject every warranty question.
    """
    compiled = []
    for term in terms:
        body = re.escape(normalize(term))
        left = r"\b" if body[:1].isalnum() else ""
        if suffixes and term in UZBEK_STEMS and body[-1:].isalnum():
            # Up to ~4 suffix syllables; still anchored, so no runaway substring hits.
            right = r"\w{0,12}\b"
        elif body[-1:].isalnum():
            right = r"\b"
        else:
            right = r"(?!\w)"
        compiled.append((term, re.compile(left + body + right, re.IGNORECASE)))
    return compiled


_ON_TOPIC_RE = _compile_terms(ON_TOPIC)
# Only the off-topic set tolerates suffixes: it is checked first, and a missed
# inflection there means an off-topic ask reaches the knowledge base.
_OFF_TOPIC_RE = _compile_terms(OFF_TOPIC, suffixes=True)
_CHITCHAT_RE = _compile_terms(CHITCHAT)


def _first_match(patterns: list[tuple[str, re.Pattern]], text: str) -> str | None:
    for term, pattern in patterns:
        if pattern.search(text):
            return term
    return None


def is_on_topic(text: str) -> tuple[bool, str]:
    """Return (allowed, reason). Reason is for logging, not for the caller."""
    if not text or not text.strip():
        return True, "empty"

    low = normalize(text)

    for pattern in _INJECTION_RE:
        if pattern.search(low):
            return False, f"injection attempt: {pattern.pattern}"

    hit = _first_match(_OFF_TOPIC_RE, low)
    if hit:
        return False, f"off-topic term: {hit}"

    hit = _first_match(_ON_TOPIC_RE, low)
    if hit:
        return True, f"on-topic term: {hit}"

    hit = _first_match(_CHITCHAT_RE, low)
    if hit:
        return True, f"conversational: {hit}"

    # Nothing matched either way. Ambiguous short input is usually a fragment of a
    # real support question ("and the mileage on that?"), so allow it and let the
    # system instruction handle it. A false refusal is worse than a weak search.
    return True, "unmatched, defaulting to allow"
