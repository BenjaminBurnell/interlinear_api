# app.py
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Dict, Any
import sqlite3, os, json

# ---------- Config ----------
DB_PATH = os.environ.get("INTERLINEAR_DB", "interlinear.sqlite3")
BOOK_CODES_PATH = os.path.join(os.path.dirname(__file__), "data", "book_codes.json")

# Fallback map (in case book_codes.json isn’t found)
FALLBACK_BOOK_CODES = {
    "GEN": "Genesis","EXO":"Exodus","LEV":"Leviticus","NUM":"Numbers","DEU":"Deuteronomy",
    "JOS":"Joshua","JDG":"Judges","RUT":"Ruth","1SA":"1 Samuel","2SA":"2 Samuel",
    "1KI":"1 Kings","2KI":"2 Kings","1CH":"1 Chronicles","2CH":"2 Chronicles","EZR":"Ezra",
    "NEH":"Nehemiah","EST":"Esther","JOB":"Job","PSA":"Psalms","PRO":"Proverbs","ECC":"Ecclesiastes",
    "SNG":"Song of Solomon","ISA":"Isaiah","JER":"Jeremiah","LAM":"Lamentations","EZK":"Ezekiel",
    "DAN":"Daniel","HOS":"Hosea","JOL":"Joel","AMO":"Amos","OBA":"Obadiah","JON":"Jonah","MIC":"Micah",
    "NAM":"Nahum","HAB":"Habakkuk","ZEP":"Zephaniah","HAG":"Haggai","ZEC":"Zechariah","MAL":"Malachi",
    "MAT":"Matthew","MRK":"Mark","LUK":"Luke","JHN":"John","ACT":"Acts","ROM":"Romans",
    "1CO":"1 Corinthians","2CO":"2 Corinthians","GAL":"Galatians","EPH":"Ephesians","PHP":"Philippians",
    "COL":"Colossians","1TH":"1 Thessalonians","2TH":"2 Thessalonians","1TI":"1 Timothy","2TI":"2 Timothy",
    "TIT":"Titus","PHM":"Philemon","HEB":"Hebrews","JAS":"James","1PE":"1 Peter","2PE":"2 Peter",
    "1JN":"1 John","2JN":"2 John","3JN":"3 John","JUD":"Jude","REV":"Revelation"
}

def load_book_codes() -> Dict[str, str]:
    try:
        with open(BOOK_CODES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)  # { "GEN": {"name":"Genesis"}, ... } OR { "GEN": "Genesis" }
        # normalize to { "GEN": "Genesis", ... }
        normalized = {}
        for k, v in data.items():
            if isinstance(v, dict) and "name" in v:
                normalized[k.upper()] = v["name"]
            else:
                normalized[k.upper()] = str(v)
        return normalized
    except Exception:
        return {k: v for k, v in FALLBACK_BOOK_CODES.items()}

BOOK_CODES = load_book_codes()
NAME_TO_CODE = {name.lower(): code for code, name in BOOK_CODES.items()}

# ---------- App ----------
app = FastAPI(title="Interlinear Bible API", version="1.0.0")

# CORS (open; lock down to your domain if you prefer)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # e.g. ["https://your-site.com"]
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def resolve_book(book_param: str) -> (str, str):
    """
    Accepts either a code like 'GEN' or a full name like 'Genesis' (case-insensitive).
    Returns (book_code, book_name). Raises if not found.
    """
    raw = (book_param or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="Book is required.")

    upper = raw.upper()
    if upper in BOOK_CODES:
        return upper, BOOK_CODES[upper]

    lower = raw.lower()
    if lower in NAME_TO_CODE:
        code = NAME_TO_CODE[lower]
        return code, BOOK_CODES[code]

    # Last resort: try first 3 letters upper (if your CSV used that)
    guess = upper[:3]
    if guess in BOOK_CODES:
        return guess, BOOK_CODES[guess]

    raise HTTPException(status_code=404, detail=f"Unknown book: {book_param}")

# ---------- Routes ----------
@app.get("/health")
def health():
    # also verify DB exists
    ok = os.path.isfile(DB_PATH)
    return {"ok": ok, "db": DB_PATH}

@app.get("/books")
def list_books():
    """
    List distinct books actually present in the DB (by code + human name).
    """
    with get_conn() as conn:
        rows = conn.execute("SELECT DISTINCT book_code FROM tokens ORDER BY book_code").fetchall()
    out = []
    for r in rows:
        code = r["book_code"]
        out.append({"code": code, "name": BOOK_CODES.get(code, code)})
    return {"books": out}

@app.get("/interlinear/{book}/{chapter:int}/{verse:int}")
def get_interlinear_verse(book: str, chapter: int, verse: int):
    """
    Return word-by-word tokens for a single verse.
    Path supports either book code (GEN) or name (Genesis).
    """
    book_code, book_name = resolve_book(book)

    with get_conn() as conn:
        cur = conn.execute(
            """
            SELECT surface, lemma, translit, gloss, morph, strong, token_index
            FROM tokens
            WHERE book_code = ? AND chapter = ? AND verse = ?
            ORDER BY token_index ASC
            """,
            (book_code, chapter, verse)
        )
        rows = cur.fetchall()

    tokens: List[Dict[str, Any]] = [
        {
            "surface": r["surface"] or "",
            "lemma": r["lemma"] or "",
            "translit": r["translit"] or "",
            "gloss": r["gloss"] or "",
            "morph": r["morph"] or "",
            "strong": r["strong"] or "",
            "index": int(r["token_index"]),
        }
        for r in rows
    ]

    return {
        "reference": f"{book_name} {chapter}:{verse}",
        "book": book_name,
        "book_code": book_code,
        "chapter": chapter,
        "verse": verse,
        "tokens": tokens
    }

@app.get("/interlinear/{book}/{chapter:int}")
def get_interlinear_chapter(book: str, chapter: int):
    """
    Optional: Return all tokens for a chapter, grouped by verse.
    Useful if you want to show a whole passage at once.
    """
    book_code, book_name = resolve_book(book)
    with get_conn() as conn:
        cur = conn.execute(
            """
            SELECT verse, token_index, surface, lemma, translit, gloss, morph, strong
            FROM tokens
            WHERE book_code = ? AND chapter = ?
            ORDER BY verse ASC, token_index ASC
            """,
            (book_code, chapter)
        )
        rows = cur.fetchall()

    by_verse: Dict[int, List[Dict[str, Any]]] = {}
    for r in rows:
        v = int(r["verse"])
        by_verse.setdefault(v, []).append({
            "surface": r["surface"] or "",
            "lemma": r["lemma"] or "",
            "translit": r["translit"] or "",
            "gloss": r["gloss"] or "",
            "morph": r["morph"] or "",
            "strong": r["strong"] or "",
            "index": int(r["token_index"]),
        })

    return {
        "reference": f"{book_name} {chapter}",
        "book": book_name,
        "book_code": book_code,
        "chapter": chapter,
        "verses": by_verse
    }
