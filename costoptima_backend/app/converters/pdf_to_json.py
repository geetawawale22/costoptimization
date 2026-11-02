#27 - 2 10:33
# -*- coding: utf-8 -*-
import os
import re
import json
import base64
import argparse
import hashlib
from io import BytesIO
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any
import pdfplumber
from PyPDF2 import PdfReader

# ====================================================
# DEFAULT CONFIG (can be overridden by CLI)
# ====================================================

PDF_FOLDER_DEFAULT = "."
OUTPUT_FOLDER_DEFAULT = "jsons"
IMAGE_FOLDER_DEFAULT = "images"

# ====================================================
# UTILITIES
# ====================================================
INVALID_WIN_CHARS = r'[\<\>:"/\\\n\?\*]'

def sanitize_filename(name: str) -> str:
    return re.sub(INVALID_WIN_CHARS, "_", (name or "")).strip()

def extract(pattern, text, default=""):
    match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
    return match.group(1).strip() if match else default

def clean_part_name(part_name, project_name):
    return (part_name or "").replace(project_name or "", "").strip()

def extract_tonnage(text: str):
    """
    Extracts machine tonnage.
    - If found, returns numeric string like '350'.
    - If not found, returns 0 (int).
    """

    # ----------------------------------------------------
    # PRIMARY: Injection Moulding M/C with optional Tonnage
    # ----------------------------------------------------
    m = re.search(
        r'(?:Injection\s*)?Mould(?:ing)?\s*(?:M/C|MC|Machine)'
        r'.{0,40}?(?:Tonnage)?'
        r'[^0-9]{0,20}'
        r'(\d{2,4})\s*(?:T\b|Ton\b|Tons\b)',
        text,
        re.IGNORECASE
    )
    if m:
        return m.group(1)

    # ----------------------------------------------------
    # EXISTING FALLBACK (kept – backward compatible)
    # ----------------------------------------------------
    m2 = re.search(r'\b(\d{2,4})\s*T\b', text, re.IGNORECASE)
    if m2:
        return m2.group(1)

    # ----------------------------------------------------
    # NEW FALLBACK: standalone "280 ton / 280 tons"
    # ----------------------------------------------------
    m3 = re.search(r'\b(\d{2,4})\s*(?:Ton\b|Tons\b)', text, re.IGNORECASE)
    if m3:
        return m3.group(1)

    return 0


def extract_date(text: str) -> str:
    specific = re.search(
        r'(?:annexure|specification|mould\s*specification|specification\s*sheet|rev(?:ision)?|date)\D{0,40}\b(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})\b',
        text,
        re.IGNORECASE
    )
    if specific:
        return specific.group(1)
    m = re.search(r'\b(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})\b', text)
    return m.group(1) if m else ""

# ====================================================
# LIFTERS & SLIDERS
# ====================================================
import re

def extract_lifters(text: str) -> int:
    """
    Extracts number after:
    - No of Lifters
    - No. of Lifters
    - No of Lifter
    """
    pattern = re.compile(
        r'''
        \bno\.?\s*of\s*      # No / No. of
        lifters?\b           # Lifter or Lifters
        [^\d]{0,10}          # optional separators (: - space)
        (\d+)                # number AFTER the word
        ''',
        re.IGNORECASE | re.VERBOSE
    )

    m = pattern.search(text)
    return int(m.group(1)) if m else 0

def extract_sliders(text: str) -> int:
    """
    Extracts number after:
    - No of Sliders
    - No. of Sliders
    - No of Slider
    """
    pattern = re.compile(
        r'''
        \bno\.?\s*of\s*      # No / No. of
        sliders?\b           # Slider or Sliders
        [^\d]{0,10}          # optional separators
        (\d+)                # number AFTER the word
        ''',
        re.IGNORECASE | re.VERBOSE
    )

    m = pattern.search(text)
    return int(m.group(1)) if m else 0

# ====================================================
# LIFTER SIZES (always dict)
# ====================================================
def extract_lifter_sizes(text: str) -> Dict[str, str]:
    def normalize_label(lbl: str) -> str:
        l = (lbl or "").lower()
        if "slider" in l and "lifter" not in l:
            return "Slider"
        if "centre" in l or "center" in l:
            return "Centre Lifters"
        if "wing" in l:
            return "Wing Lifter"
        if "moving" in l:
            return "Moving Lifter"
        if "lifter" in l:
            return "Lifter"
        return ""

    # label_block_rx: Extracts chunks of text starting with a lifter/slider variant
    label_block_rx = re.compile(
        r'(?P<label>(?:[a-zA-Z]{2,10}\s+)?(?:lifter[s]?|ifter[s]?|slider[s]?|lider[s]?|cav|inserts?))'
        r'\s*[-:]*\s*'
        r'(?P<tail>.*?)'
        r'(?=(?:\b(?:[a-zA-Z]{2,10}\s+)?(?:lifter[s]?|ifter[s]?|slider[s]?|lider[s]?|cav|inserts?)\b)|$)',
        re.IGNORECASE | re.DOTALL
    )
    
    # size_qty_rx: Extracts the dimension and quantity from the tail
    size_qty_rx = re.compile(
        r'(?P<L>\d+(?:\.\d+)?)\s*[xX×*]\s*(?P<W>\d+(?:\.\d+)?)\s*[xX×*]\s*(?P<H>\d+(?:\.\d+)?)'
        r'\s*(?:mm)?\s*[-–—,\s]*(?:qty\.?\s*)?(?P<qty>\d+)\s*(?:nos?|no\.?|n)?',
        re.IGNORECASE
    )

    store: Dict[str, List[Tuple[str, str, str, str]]] = {}

    for m in label_block_rx.finditer(text):
        raw_label = m.group("label").strip()
        # Clean up cases where 'nos' was mistakenly captured as prefix to Sliders
        cat = re.sub(r'(?i)\b(?:nos?|qty|no|n)\b\s*', '', raw_label).strip()
        cat = normalize_label(cat)
        if not cat:
            continue
        tail = m.group("tail") or ""
        for t in size_qty_rx.finditer(tail):
            L, W, H, q = t.group("L"), t.group("W"), t.group("H"), t.group("qty")
            store.setdefault(cat, []).append((L, W, H, q))

    if not store:
        return {}

    out: Dict[str, str] = {}
    for cat, items in store.items():
        seen = set()
        formatted: List[str] = []
        for (L, W, H, q) in items:
            key = (L, W, H, q)
            if key in seen:
                continue
            seen.add(key)
            formatted.append(f"{L}x{W}x{H}mm-{int(q)}no")
        out[cat] = ", ".join(formatted)
    return out

# ====================================================
# CORE/CAVITY MATERIAL GRADES (NEW)
# ====================================================
import re

def extract_core_cavity_material_grades(text: str):
    """
    Returns (core_grade, cavity_grade) or ("", "").
    """
    def clean_token(s: str) -> str:
        s = (s or "").strip()
        s = re.split(r"\s*(?:/|\(|,|;).*", s)[0].strip()
        s = re.sub(r"\bEquivalent.*$", "", s, flags=re.I).strip()
        s = re.sub(r"\bBUDERUS\b|\bBUDURUS\b", "", s, flags=re.I).strip()
        s = re.sub(r"\bNITRID(?:ED|ING)?\b", "", s, flags=re.I).strip()
        
        # --- PATCH: insert space between unit and glued labels like 'mmCore' / 'mmCavity' ---
        s = re.sub(r'(?i)\bmm(?=core\b)', 'mm ', s)
        s = re.sub(r'(?i)\bmm(?=cavity\b)', 'mm ', s)
        s = re.sub(r'(?i)(?<=\d)mm(?=\s*(?:core|cavity)\b)', ' mm', s)
        s = re.sub(r"\s+", " ", s)
        return s

    def is_dimension_like(s: str) -> bool:
        return bool(re.search(r"\d\s*[xX]\s*\d", s))

    def is_material_token(s: str) -> bool:
        s = s.strip()
        if not s or s.isdigit():
            return False
        if is_dimension_like(s):
            return False
        if re.search(r"\bTSHH\b", s, re.I):
            return True
        if re.search(r"\b1?\.?2738\b", s, re.I):
            return True
        if re.search(r"\bHH\b", s, re.I):
            return True
        return False

    txt = re.sub(r"\s+", " ", text or "")

    m = re.search(r"\bCore\b\s*[-:]\s*([A-Za-z0-9.\-+ ]{1,80}?)\s*(?:,|;|&|/)\s*Cavity\b\s*[-:]\s*([A-Za-z0-9.\-+ ]{1,80})", txt, re.I)
    if m:
        a, b = clean_token(m.group(1)), clean_token(m.group(2))
        if is_material_token(a) and is_material_token(b):
            return a, b

    m = re.search(r"\bCore\s*&\s*Cavity\b\s*[-:]\s*([A-Za-z0-9.\-+ ]{1,80})\s*&\s*([A-Za-z0-9.\-+ ]{1,80})", txt, re.I)
    if m:
        a, b = clean_token(m.group(1)), clean_token(m.group(2))
        if is_material_token(a) and is_material_token(b):
            return a, b

    m = re.search(r"\bCore\s*[&/]\s*Cavity\b\s*[-:]\s*([A-Za-z0-9.\-+ ]{1,80})\s*[&/]\s*([A-Za-z0-9.\-+ ]{1,80})", txt, re.I)
    if m:
        a, b = clean_token(m.group(1)), clean_token(m.group(2))
        if is_material_token(a) and is_material_token(b):
            return a, b

    m = re.search(r"\bCore\b\s+([A-Za-z0-9.\-+ ]{1,80})\s*&\s*Cavity\b\s*[-:]\s*([A-Za-z0-9.\-+ ]{1,80})", txt, re.I)
    if m:
        a, b = clean_token(m.group(1)), clean_token(m.group(2))
        if is_material_token(a) and is_material_token(b):
            return a, b

    m = re.search(
        r"\bCore\s*&\s*Cavity\b\s*[-:]?\s*([A-Za-z0-9.\-+ ]{1,80}?)(?=\s*(?:$|\b(?:For|Mould|Wear|Guide|OHNS|Core|Cavity)\b))",
        txt, re.I
    )
    if m:
        v = clean_token(m.group(1))
        if is_material_token(v):
            return v, v
    # ✅ NEW: handle "Cavity - X Core - Y" order
    m = re.search(
        r"\bCavity\b\s*[-:]\s*([A-Za-z0-9.\-+ ()]{1,80}?)\s*"
        r"\bCore\b\s*[-:]\s*([A-Za-z0-9.\-+ ()]{1,80})",
        txt,
        re.I
    )
    if m:
        cav = clean_token(m.group(1))
        core = clean_token(m.group(2))
        if is_material_token(core) and is_material_token(cav):
            return core, cav    

    core_only = ""
    cav_only = ""

    m = re.search(r"\bCore\b\s*[-:]?\s*([A-Za-z0-9.\-+ ]{1,80})\b", txt, re.I)
    if m:
        t = clean_token(m.group(1))
        if is_material_token(t):
            core_only = t

    m = re.search(r"\bCavity\b\s*[-:]?\s*([A-Za-z0-9.\-+ ]{1,80})\b", txt, re.I)
    if m:
        t = clean_token(m.group(1))
        if is_material_token(t):
            cav_only = t

    if core_only or cav_only:
        #v = core_only or cav_only
        #return v, v
        return core_only, cav_only
    

    return "", ""

def extract_slider_lifter_material_grades(text: str):
    """
    Robust extraction for Slider and Lifter material grades.
    """
    def normalize_grade(s: str) -> str:
        s = (s or "").upper()
        # strip vendor/treatment tails
        s = re.sub(r"\bBUDERUS\b|\bBUDURUS\b", "", s)
        s = re.sub(r"\bNITRID(?:ED|ING)\b", "", s)
        s = re.sub(r"\bEQUIVALENT.*$", "", s)
        # unify & canonicalize
        s = s.replace("1.2738", "2738")
        s = re.sub(r"\s+", " ", s).strip()
        s = re.sub(r"\b(2738)\s*TSHH\b", r"\1 TSHH", s)  # 2738TSHH -> 2738 TSHH
        if re.search(r"\b2738 TSHH\b", s): return "2738 TSHH"
        if re.search(r"\bTSHH\b", s): return "TSHH"
        if re.search(r"\b2738\b", s): return "2738"
        return ""

    # flatten to one line for span searches
    txt = re.sub(r"\s+", " ", text or "")

    # ---------- A) INLINE ----------
    m_inline = re.search(
        r"(?i)Sliders?\s*/\s*Lifters?\s*/\s*Pad\s*ejectors\s*[-:]\s*([A-Za-z0-9.\-+/() ]{1,160})",
        txt
    )
    if m_inline:
        tok = normalize_grade(m_inline.group(1))
        if tok:
            # keep your current bumper behavior (lifter with trailing space)
            return tok, tok + " "

    # ---------- B) TABLE: Slider row ----------
    token_slider = re.compile(
        r"(?is)\bslider\b.{0,160}\bangle\b.{0,60}\bejector(?:s)?\b.{0,200}\bsub\b.{0,40}insert(?:s)?\b"
    )
    material_rx = re.compile(
        r"(?i)\b((?:1\.)?2738\s*TSHH|2738TSHH|(?:1\.)?2738\s*HH|(?:1\.)?2738|TSHH)\b"
    )

    slider_grade = ""
    lifter_grade = ""
    slider_tok_abs = None  # absolute position of the slider token for collision checks

    m_lab = token_slider.search(txt)
    if m_lab:
        # forward window (right-hand cell in tables)
        fstart = m_lab.end()
        fwin = txt[fstart:fstart + 10000]
        m_fw = material_rx.search(fwin)
        if m_fw:
            slider_grade = normalize_grade(m_fw.group(1))
            slider_tok_abs = fstart + m_fw.start()
        else:
            # backward window (some PDFs output value before label)
            b_end = max(0, m_lab.start())
            bstart = max(0, b_end - 4000)
            bwin = txt[bstart:b_end]

            # Find the nearest (last) material token in the backward window
            m_bw_nearest = None
            for mm in material_rx.finditer(bwin):
                m_bw_nearest = mm

            if m_bw_nearest:
                slider_grade = normalize_grade(m_bw_nearest.group(1))
                slider_tok_abs = bstart + m_bw_nearest.start()

    # ---------- C) Lifter: only if there is an explicit lifter label + nearby token ----------
    lifter_label_rx = re.compile(
        r"(?i)\b(lifter|lifters|moving\s*lifter|wing\s*lifter|centre\s*lifter|center\s*lifter)\b"
    )
    m_lif = lifter_label_rx.search(txt)
    if m_lif:
        lstart = m_lif.end()
        lwin = txt[lstart:lstart + 300]  # tight, local window
        m_lmat = material_rx.search(lwin)
        if m_lmat:
            lifter_abs = lstart + m_lmat.start()
            # reject if it's the same position as the slider token, or if local pre-context mentions "slider"
            same_as_slider = slider_tok_abs is not None and abs(lifter_abs - slider_tok_abs) <= 5
            pre_local = lwin[:m_lmat.start()]
            mentions_slider = re.search(r"(?i)\bslider\b", pre_local) is not None
            if not same_as_slider and not mentions_slider:
                lifter_grade = normalize_grade(m_lmat.group(1))

    # ---------- D) Failsafe ----------
    if not slider_grade and lifter_grade and m_lab:
        slider_grade, lifter_grade = lifter_grade, ""
    pattern_slider_num = 0
    pattern_lifter_num = 0
    pattern_eject = re.compile(r"(ejectors?/?)")
    
    if (not slider_grade and not lifter_grade) or (len(slider_grade)==0 and len(lifter_grade)==0) :
        for line in text.split('\n'):
            if pattern_eject.search(line):
                pattern_slider = re.compile(
                    r"""
                    (?:
                        (?<![A-Za-z])           # no letter before
                        Slider s?
                        \s*/\s*
                      |
                        \s*/\s*
                        Slider s?
                        (?![A-Za-z])            # no letter after
                    )
                    """,
                    re.VERBOSE,
                )
                if pattern_slider.search(line):
                    pattern_slider_num = 1
                
                pattern_lifter = re.compile(
                    r"""
                    (?:                         # either side of the slash
                        (?<![A-Za-z])           # no letter immediately before (digits/punct OK)
                        Lifter s?               # 'Lifter' or 'Lifters' (case-sensitive)
                        \s*/\s* # slash with optional spaces
                      |
                        \s*/\s* # slash with optional spaces
                        Lifter s?               # 'Lifter' or 'Lifters' (case-sensitive)
                        (?![A-Za-z])            # no letter immediately after (digits/punct OK)
                    )
                    """,
                    re.VERBOSE,                 # NOTE: no IGNORECASE → case-sensitive
                )

                if pattern_lifter.search(line):
                    pattern_lifter_num = 1

    # Initialize variable to prevent UnboundLocalError
    pattern_grade_in_str = ""
    
    for line in text.split('\n'):
        pattern_grade_in = re.compile(
            r"""
            ^                                   # must start at beginning (no leading char/space)
            (?:                                 # ---- token choices ----
                1\.2738                         # literal 1.2738
                (?:                             # optional suffix group
                    \s* # space or no space before suffix
                    (?:                         # the suffix itself
                        TSHH(?:\s+Nitr(?:a|i)ted)?   # TSHH [Nitraited/Nitrided] optional
                      | HH                           # or HH
                      | HHHG                         # or HHHG
                    )
                )?                              # suffix optional overall
              |                                 # OR
                TSHH(?:\s+Nitr(?:a|i)ted)?      # standalone TSHH [Nitraited/Nitrided]
            )
            \b                                  # clean token end (don’t run into trailing letters)
            \s* # allow spaces after the token
            """,
            re.IGNORECASE | re.VERBOSE
        )

        if pattern_grade_in.search(line):
            pattern_grade_in_str = pattern_grade_in.search(line).group(0)

    # Added safety check when assigning
    if (not slider_grade or len(slider_grade)==0) and pattern_slider_num == 1:
        slider_grade = str(pattern_grade_in_str) if pattern_grade_in_str else ""
    if (not lifter_grade or len(lifter_grade)==0) and pattern_lifter_num == 1:
        lifter_grade = str(pattern_grade_in_str) if pattern_grade_in_str else ""    

    return slider_grade, lifter_grade


# PART SIZE
# ====================================================
def extract_part_size(text: str) -> str:
    ctx = re.search(r"Part\s*Size\s*[:\-]?\s*(\d+)\s*[xX×*]\s*(\d+)\s*[xX×*]\s*(\d+)", text, re.IGNORECASE)
    if ctx:
        l, h, w = ctx.groups()
        return f"{l} x {h} x {w}"
    match = re.search(r"(\d+)\s*[xX×*]\s*(\d+)\s*[xX×*]\s*(\d+)", text)
    if match:
        length, height, width = match.groups()
        return f"{length} x {height} x {width}"
    return ""

# ====================================================
# Core/Cavity Block & Insert parser (candidate-based; robust against "mmCore hsg" and OCR breaks)
# ====================================================
import re
from typing import Dict, Optional, Tuple

Dim3 = Tuple[int, int, int]

# ================= Regex atoms =================
ROLE_CORE = r"(?:core|cor|cores)"
ROLE_CAV  = r"(?:cavity|cav|cavities)"
ROLE      = rf"(?:{ROLE_CORE}|{ROLE_CAV})"

BRACKET_TOKEN = r"(?:\[(?:\s*block\s*|\s*housing\s*|\s*hsg\s*)\])"
PLAIN_TOKEN   = r"(?:block|housing|hsg)"
INS_TOKEN     = r"(?:insert|ins)"

SEP_ANY    = r"[\s\W]*"
TOKEN_ANY  = rf"(?:{BRACKET_TOKEN}|{PLAIN_TOKEN}|{INS_TOKEN})"
OPT_TOKENS = rf"(?:{SEP_ANY}{TOKEN_ANY})*"

# Dimensions: L x W x H or L x W x H1/H2
DIMS    = r"(\d{2,5})\s*[x×X]\s*(\d{2,5})\s*[x×X]\s*(\d{2,5})(?:\s*/\s*(\d{2,5}))?"
DIMS_RE = re.compile(DIMS)

PAIR_SEP = r"(?:/|[\s\W]{2,})"

PAIR_ROLES  = rf"\b({ROLE})\b{OPT_TOKENS}{SEP_ANY}{PAIR_SEP}{SEP_ANY}({ROLE})\b{OPT_TOKENS}"
SINGLE_ROLE = rf"\b({ROLE})\b{OPT_TOKENS}"

PAIR_LINE   = re.compile(rf"{PAIR_ROLES}{SEP_ANY}[-–—:]+{SEP_ANY}{DIMS}", re.IGNORECASE)
SINGLE_LINE = re.compile(rf"{SINGLE_ROLE}{SEP_ANY}[-–—:]+{SEP_ANY}{DIMS}", re.IGNORECASE)

HAS_INSERT = re.compile(rf"\b{INS_TOKEN}\b", re.IGNORECASE)

# ================= Helpers =================
def normalize(text: str) -> str:
    t = text.replace("\u00D7", "x").replace("–", "-").replace("—", "-")
    t = re.sub(r"\s*\n\s*", "\n", t)
    return t

def parse_dims_from_string(s: str):
    m = DIMS_RE.search(s)
    if not m:
        raise ValueError("No dimensions found")
    a, b, c1 = int(m.group(1)), int(m.group(2)), int(m.group(3))
    c2 = int(m.group(4)) if m.group(4) else None
    return a, b, c1, c2

def norm_role(role: str) -> str:
    if re.fullmatch(ROLE_CORE, role, re.I):
        return "core"
    if re.fullmatch(ROLE_CAV, role, re.I):
        return "cavity"
    return role.lower()

# ================= MAIN FUNCTION =================
def parse_core_cavity(text: str) -> Dict[str, str]:
    t = normalize(text)

    result = {
        "block":  {"core": None, "cavity": None},
        "insert": {"core": None, "cavity": None},
    }

    for line in [l.strip() for l in t.splitlines() if l.strip()]:

        if not re.search(rf"\b({ROLE_CORE}|{ROLE_CAV})\b", line, re.I):
            continue

        bucket = "insert" if HAS_INSERT.search(line) else "block"

        # -------- Paired: Core & Cavity ... H1/H2 --------
        mp = PAIR_LINE.search(line)
        if mp:
            r1 = norm_role(mp.group(1))
            r2 = norm_role(mp.group(2))
            a, b, c1, c2 = parse_dims_from_string(mp.group(0))
            if c2 is None:
                continue

            # ✅ RESPECT ORDER — NO AUTO SWAP
            if r1 == "core" and r2 == "cavity":
                result[bucket]["core"]   = (a, b, c1)
                result[bucket]["cavity"] = (a, b, c2)
            elif r1 == "cavity" and r2 == "core":
                result[bucket]["cavity"] = (a, b, c1)
                result[bucket]["core"]   = (a, b, c2)

            continue

        # -------- Single-role line --------
        ms = SINGLE_LINE.search(line)
        if ms:
            role = norm_role(ms.group(1))
            a, b, c1, _ = parse_dims_from_string(ms.group(0))
            result[bucket][role] = (a, b, c1)

    def fmt(d: Optional[Dim3]) -> str:
        return f"{d[0]}x{d[1]}x{d[2]}" if d else ""
    # ====================================================
    # FIX: Handle "Cavity/Core blocks : 690 x 425 x 120/140"
    # ====================================================
    if not result["block"]["core"] and not result["block"]["cavity"]:
        m = re.search(
            r'\b(Cavity\s*/\s*Core|Core\s*/\s*Cavity)\s*blocks?\s*[:\-]?\s*'
            r'(\d{2,5})\s*[xX]\s*(\d{2,5})\s*[xX]\s*(\d{2,5})\s*/\s*(\d{2,5})',
            text,
            re.IGNORECASE
        )

        if m:
            L = int(m.group(2))
            W = int(m.group(3))
            v1 = int(m.group(4))
            v2 = int(m.group(5))

            if m.group(1).lower().startswith("cavity"):
                result["block"]["cavity"] = (L, W, v1)
                result["block"]["core"] = (L, W, v2)
            else:
                result["block"]["core"] = (L, W, v1)
                result["block"]["cavity"] = (L, W, v2)
    # ====================================================
    # ✅ NEW FIX: Handle "Core & cavity 480 X 440 X 165 / 155"
    # ====================================================
    if not result["block"]["core"] and not result["block"]["cavity"]:
        m = re.search(
            r'\bCore\s*&\s*Cavity\b\s*'
            r'(?:blocks?)?\s*[:\-]?\s*'
            r'(\d{2,5})\s*[xX]\s*(\d{2,5})\s*[xX]\s*'
            r'(\d{2,5})\s*/\s*(\d{2,5})',
            t,
            re.IGNORECASE
        )
        if m:
            L = int(m.group(1))
            W = int(m.group(2))
            core_h = int(m.group(3))
            cav_h = int(m.group(4))

            result["block"]["core"] = (L, W, core_h)
            result["block"]["cavity"] = (L, W, cav_h)            

    # ✅ FIX: Handle "Core cavity insert - 260X150X60 / 50"
    if not result["insert"]["core"] or not result["insert"]["cavity"]:
        
        m = re.search(
            r'\bCore\s+Cavity\s+(?:Insert|Ins)\b\s*[-:]\s*'
            r'(\d{2,5})\s*[xX]\s*(\d{2,5})\s*[xX]\s*(\d{2,5})\s*/\s*(\d{2,5})',
            t,
            re.IGNORECASE
        )


        if m:
            L, W, core_h, cav_h = map(int, m.groups())
            result["insert"]["core"] = (L, W, core_h)
            result["insert"]["cavity"] = (L, W, cav_h)
        if not m:
            m = re.search(
                r'\bCavity\s+Core\s+(?:Insert|Ins)\b\s*[-:]\s*'
                r'(\d{2,5})\s*[xX]\s*(\d{2,5})\s*[xX]\s*(\d{2,5})\s*/\s*(\d{2,5})',
                t,
                re.IGNORECASE
            )


            if m:
                L, W,cav_h, core_h  = map(int, m.groups())
                result["insert"]["core"] = (L, W, core_h)
                result["insert"]["cavity"] = (L, W, cav_h)    
    # ====================================================
    # ✅ NEW FIX: Handle "Core & cavity Insert 770 X 465 X 135 / 125"
    # ====================================================
    if not result["insert"]["core"] or not result["insert"]["cavity"]:
        m = re.search(
            r'\bCore\s*&\s*Cavity\s+Insert\b\s*[:\-]?\s*'
            r'(\d{2,5})\s*[xX]\s*(\d{2,5})\s*[xX]\s*'
            r'(\d{2,5})\s*/\s*(\d{2,5})',
            t,
            re.IGNORECASE
        )
        if m:
            L = int(m.group(1))
            W = int(m.group(2))
            core_h = int(m.group(3))
            cav_h = int(m.group(4))

            result["insert"]["core"] = (L, W, core_h)
            result["insert"]["cavity"] = (L, W, cav_h)
    # ====================================================
    # ✅ FIX: Correct Cavity Insert height when split H1/H2 exists
    # Example:
    # Core & Cavity insert - 520 X 510 X 170/160
    # ====================================================
    if (
        result["insert"]["core"]
        and result["insert"]["cavity"]
        and result["insert"]["core"] == result["insert"]["cavity"]
    ):
        m = re.search(
            r'\bCore\s*(?:/|&)\s*Cavity\s*Insert\b\s*[-:]\s*'
            r'(\d{2,5})\s*[xX]\s*(\d{2,5})\s*[xX]\s*(\d{2,5})\s*/\s*(\d{2,5})',
            t,
            re.IGNORECASE
        )
        if m:
            L = int(m.group(1))
            W = int(m.group(2))
            core_h = int(m.group(3))
            cav_h = int(m.group(4))

            result["insert"]["core"]   = (L, W, core_h)
            result["insert"]["cavity"] = (L, W, cav_h)        
    
    # ====================================================
    # ✅ FINAL FIX: handle multiple Core/Cavity items
    # on the SAME LINE (comma-separated)
    # Example:
    # "Cavity : 2500x1200x1000 mm, Core Insert: 1750x1200x680 mm"
    # ====================================================
    if (
        not result["block"]["core"]
        or not result["block"]["cavity"]
        or not result["insert"]["core"]
    ):
    
        for raw_line in t.splitlines():
        
            # split by comma → parse each piece
            parts = [p.strip() for p in raw_line.split(",")]
    
            for part in parts:
            
                # ---- Cavity Block ----
                m = re.search(
                    r'\bCavity\b\s*[:\-]\s*(\d{3,5})\s*[xX]\s*(\d{3,5})\s*[xX]\s*(\d{3,5})',
                    part,
                    re.IGNORECASE
                )
                if m:
                    result["block"]["cavity"] = tuple(map(int, m.groups()))
                    continue
                
                # ---- Core Block (hsg / housing) ----
                m = re.search(
                    r'\bCore\b\s*(?:hsg|housing)?\s*[:\-]\s*'
                    r'(\d{3,5})\s*[xX]\s*(\d{3,5})\s*[xX]\s*(\d{3,5})',
                    part,
                    re.IGNORECASE
                )
                if m:
                    result["block"]["core"] = tuple(map(int, m.groups()))
                    continue
                
                # ---- Core Insert ----
                m = re.search(
                    r'\bCore\s*(?:Insert|Ins)\b\s*[:\-]\s*'
                    r'(\d{3,5})\s*[xX]\s*(\d{3,5})\s*[xX]\s*(\d{3,5})',
                    part,
                    re.IGNORECASE
                )
                if m:
                    result["insert"]["core"] = tuple(map(int, m.groups()))
                    continue
                
                # ---- Cavity Insert ----
                m = re.search(
                    r'\bCavity\s*(?:Insert|Ins)\b\s*[:\-]\s*'
                    r'(\d{3,5})\s*[xX]\s*(\d{3,5})\s*[xX]\s*(\d{3,5})',
                    part,
                    re.IGNORECASE
                )
                if m:
                    result["insert"]["cavity"] = tuple(map(int, m.groups()))
    
            if (
                result["block"]["core"]
                and result["block"]["cavity"]
                and result["insert"]["core"]
            ):
                break
    # ====================================================
    # ✅ SAFE ADDITIVE FIX (NO IMPACT ON OLD CODE)
    # Handles:
    # Core / Cavity [block][Housing][Hsg] - 1670 x 970 x 620/390
    # Core & Cavity [block][Housing][Hsg] - 1670 x 970 x 620/390
    # ====================================================
    if not result["block"]["core"] and not result["block"]["cavity"]:
        m = re.search(
            r'\bCore\s*(?:/|&)\s*Cavity\b\s*'
            r'(?:\[\s*(?:block|housing|hsg)\s*\]\s*){0,3}'
            r'[-–—:]\s*'
            r'(\d{2,5})\s*[xX]\s*(\d{2,5})\s*[xX]\s*(\d{2,5})\s*/\s*(\d{2,5})',
            t,
            re.IGNORECASE
        )
        if m:
            L = int(m.group(1))
            W = int(m.group(2))
            core_h = int(m.group(3))
            cav_h  = int(m.group(4))

            result["block"]["core"]   = (L, W, core_h)
            result["block"]["cavity"] = (L, W, cav_h)


    # ====================================================
    # ✅ FIX: Core & Cavity housing / block sizes
    # Example:
    # Core & Cavity housing - 730 X 720 X 245/230
    # ====================================================
    if not result["block"]["core"] and not result["block"]["cavity"]:
        m = re.search(
            r'\bCore\s*(?:/|&)\s*Cavity\b\s*'
            r'(?:housing|block|hsg)\b\s*[-:]\s*'
            r'(\d{2,5})\s*[xX]\s*(\d{2,5})\s*[xX]\s*'
            r'(\d{2,5})\s*/\s*(\d{2,5})',
            t,
            re.IGNORECASE
        )
        if m:
            L = int(m.group(1))
            W = int(m.group(2))
            core_h = int(m.group(3))
            cav_h  = int(m.group(4))

            result["block"]["core"]   = (L, W, core_h)
            result["block"]["cavity"] = (L, W, cav_h)

    # ====================================================
    # ✅ FIX: Handle "Core / Cavity- 1100X510X165 / 155"
    # ====================================================
    if (
        result["block"]["core"]
        and result["block"]["cavity"]
        and result["block"]["core"] == result["block"]["cavity"]
    ):
        m = re.search(
            r'\bCore\s*/\s*Cavity\s*[-:]?\s*'
            r'(\d{2,5})\s*[xX]\s*(\d{2,5})\s*[xX]\s*(\d{2,5})\s*/\s*(\d{2,5})',
            t,
            re.IGNORECASE
        )
        if m:
            L = int(m.group(1))
            W = int(m.group(2))
            core_h = int(m.group(3))
            cav_h  = int(m.group(4))

            result["block"]["core"]   = (L, W, core_h)
            result["block"]["cavity"] = (L, W, cav_h) 
    # ====================================================
    # ✅ FIX: Handle "Core & Cavity - 1081X748X424/112"
    # ====================================================
    if (
        result["block"]["core"]
        and result["block"]["cavity"]
        and result["block"]["core"] == result["block"]["cavity"]
    ):
        m = re.search(
            r'\bCore\s*(?:/|&)\s*Cavity\b\s*[-:]\s*'
            r'(\d{2,5})\s*[xX]\s*(\d{2,5})\s*[xX]\s*(\d{2,5})\s*/\s*(\d{2,5})',
            t,
            re.IGNORECASE
        )
        if m:
            L = int(m.group(1))
            W = int(m.group(2))
            core_h = int(m.group(3))
            cav_h  = int(m.group(4))

            result["block"]["core"]   = (L, W, core_h)
            result["block"]["cavity"] = (L, W, cav_h)               
    # ====================================================
    # ✅ FINAL FIX: Protect split Core/Cavity Insert heights
    # ====================================================
    ci = result["insert"]["core"]
    cavi = result["insert"]["cavity"]

    if ci and cavi:
        # If dimensions match but heights were accidentally flattened
        if ci[0] == cavi[0] and ci[1] == cavi[1] and ci[2] == cavi[2]:
            m = re.search(
                r'\bCore\s*(?:/|&)\s*Cavity\s+Insert\b\s*[-:]\s*'
                r'(\d{2,5})\s*[xX]\s*(\d{2,5})\s*[xX]\s*(\d{2,5})\s*/\s*(\d{2,5})',
                t,
                re.IGNORECASE
            )
            if m:
                L, W, core_h, cav_h = map(int, m.groups())
                result["insert"]["core"]   = (L, W, core_h)
                result["insert"]["cavity"] = (L, W, cav_h)        

            
    out_core_block   = fmt(result["block"]["core"])
    out_cav_block    = fmt(result["block"]["cavity"])
    out_core_insert  = fmt(result["insert"]["core"])
    out_cav_insert   = fmt(result["insert"]["cavity"])

    return {
        "Core Block":   out_core_block,
        "Cavity Block": out_cav_block,
        "Core Insert":  out_core_insert,
        "Cavity Insert": out_cav_insert,
    }


# ====================================================
# MOULD WEIGHT TO KG
# ====================================================
def parse_mould_weight_to_kg(text: str):
    m = re.search(
        r'Mould\s*Weight.*?(\d+(\.\d+)?)\s*(kg|kgs?|kilogram|ton|tons?|t)?',
        text,
        re.IGNORECASE | re.DOTALL
    )
    if not m:
        return ""
    val = float(m.group(1))
    unit = (m.group(3) or "").lower()
    if unit in {"ton", "tons", "t"}:
        return int(val * 1000)
    elif not unit.startswith('k'):
        return int(val * 1000)
    return float(val)

# ====================================================
# IMAGE EXTRACTION (general; filtered, deduped)
# ====================================================
def _save_and_hash_pil(pil_image) -> Tuple[str, bytes]:
    tmp = BytesIO()
    pil_image.save(tmp, format="PNG")
    raw = tmp.getvalue()
    sha1 = hashlib.sha1(raw).hexdigest()
    return sha1, raw

def extract_mould_images(
    pdf_pages,
    part_name: str,
    image_folder: Path,
    max_images: int = 4,
    resolution: int = 150,
    include_base64: bool = True,
    min_width: int = 120,
    min_height: int = 120,
    debug: bool = False,
):
    part_name_safe = sanitize_filename(part_name or "part")
    image_folder.mkdir(parents=True, exist_ok=True)
    collected = []
    seen_hashes = set()
    img_counter = 0

    for page_number, page in enumerate(pdf_pages, start=1):
        if img_counter >= max_images:
            break
        imgs = getattr(page, "images", None)
        if not imgs:
            continue
        try:
            imgs_sorted = sorted(imgs, key=lambda im: im.get("x0", 0))
        except Exception:
            imgs_sorted = imgs

        for im in imgs_sorted:
            if img_counter >= max_images:
                break
            x0 = im.get("x0", im.get("left"))
            x1 = im.get("x1", im.get("right"))
            y0 = im.get("y0", im.get("top"))
            y1 = im.get("y1", im.get("bottom"))
            if None in (x0, x1, y0, y1):
                continue
            width_est = abs(x1 - x0)
            height_est = abs(y1 - y0)
            if width_est < min_width or height_est < min_height:
                continue
            bbox = (x0, y0, x1, y1)
            try:
                cropped = page.within_bbox(bbox).to_image(resolution=resolution)
                sha1, raw = _save_and_hash_pil(cropped.original)
                if sha1 in seen_hashes:
                    continue
                seen_hashes.add(sha1)
                img_counter += 1
                image_filename = f"{part_name_safe}_{page_number}_{img_counter}.png"
                image_path = image_folder / image_filename
                with open(image_path, "wb") as fout:
                    fout.write(raw)
                img_record = {
                    "index": img_counter,
                    "filename": image_filename,
                    "path": str(image_path),
                    "width": int(round(width_est)),
                    "height": int(round(height_est)),
                    "page": page_number,
                }
                if include_base64:
                    img_record["base64"] = base64.b64encode(raw).decode()
                collected.append(img_record)
            except Exception:
                continue
    return collected

# ====================================================
# IMAGE: backup1.py-style MOULD IMAGE extractor (ACTIVE)
# ====================================================
def extract_mould_images_backup(
    pdf_pages,
    part_name: str,
    image_folder: Path,
    resolution: int = 75,
    debug: bool = False,
):
    import gc
    records = []
    image_folder.mkdir(parents=True, exist_ok=True)
    part_name_safe = sanitize_filename(part_name or "Mould_Image")
    
    for page_number, page in enumerate(pdf_pages, start=1):
        page_text = (page.extract_text() or "")
        if not re.search(r'\b(Mould\s*image|A\s*SURFACE\s*IMAGE|B\s*SURFACE\s*IMAGE)\b',
                         page_text, re.IGNORECASE):
            continue
        if not page.images:
            if debug:
                print(f"[DEBUG] Page {page_number} has 'Mould image' but no images.")
            continue

        try:
            images_sorted = sorted(page.images, key=lambda img: img.get("x0", 0))
        except Exception:
            images_sorted = list(page.images)

        for idx, img in enumerate(images_sorted[:2], start=1):
            try:
                bbox = (img.get("x0"), img.get("top"), img.get("x1"), img.get("bottom"))
                if None in bbox:
                    continue
                cropped = page.within_bbox(bbox).to_image(resolution=resolution)
                image_filename = f"{part_name_safe}_{idx}.png"
                image_path = image_folder / image_filename
                # Optimize: Save as compressed PNG (avoid base64 to save memory)
                cropped.original.save(str(image_path), format="PNG", optimize=True)
                records.append({
                    "index": idx,
                    "filename": image_filename,
                    "path": str(image_path),
                })
                # Clear memory after each image
                del cropped
                gc.collect()
            except Exception:
                continue
    return records

# ====================================================
# SUPPORT: Extract number of cavities
# ====================================================
def extract_no_of_cavity(lines: List[str]) -> int:
    for line in lines:
        clean_line = line.lower().replace(".", "")
        pattern = re.compile(
            r"""(?i)\b(?:no(?:s)?\.?\s*of|no(?:s)?of)[\s.:\_-]*cavit(?:y|ies)(?=\b|\d)[^\r\n]*?
            (?P<count>(?:\d{1,3}(?:,\d{3})*|\d+)(?:\s*\+\s*(?:\d{1,3}(?:,\d{3})*|\d+))?)""",
            re.VERBOSE
        )
        match = pattern.search(clean_line)
        if not match:
            match = re.search(r'\bcavit(?:y|ies)\b.*?(\d+\s*\+\s*\d+|\d+)', clean_line)
        try:
            if match:
                val = match.group(1).replace(" ", "")
                if "+" in val:
                    return sum(map(int, val.split("+")))
                else:
                    return int(val)
        except:
            return 0
    return 0

# ====================================================
# PART FINISH FINALIZER (NEW)
# ====================================================
def normalize_part_finish(value: str) -> str:
    v_raw = (value or "").strip()
    v = v_raw.lower()

    def is_blank(s: str) -> bool:
        return s.strip() == ""

    na_tokens = {"na", "n.a.", "n/a"}
    painted_exact = {"paint", "painting", "painted"}

    if v in painted_exact or re.search(r'\bno\b.*\bpainted\b', v) or any(tok in v for tok in ["painted", "painting", " paint"]):
        return "Painted Parts"
    if v in na_tokens:
        return "Painted Parts"
    if v in {"no", "chrome"} or re.search(r'\bno\s*\(?\s*mic\s*\)?', v) or ("mirror" in v) or ("high gloss" in v):
        return "Mirror Finish / High Gloss"
    if v == "yes" or is_blank(v):
        return "Textured Parts"
    if re.search(r'[a-z0-9]', v):
        return "Textured Parts"
    if "grain" in v or v in {"tdb", "tbd"}:
        return "Textured Parts"
    return "Textured Parts"

def extract_part_material(full_text: str) -> str:
    """
    Fixes cases where PDF text merges words like: 'supplierTPE'
    by scanning for material tokens even if glued to other text.
    """
    txt = re.sub(r"\s+", " ", full_text or "")

    material_tokens = [
        "TPE", "TPU", "TPO", "TPEE", "TPE-S",
        "PP", "PA", "PA6", "PA66", "ABS", "PC", "PC\\+ABS",
        "POM", "PBT", "PET", "NYLON", "EPDM"
    ]

    label_section = ""
    label_match = re.search(
        r"(Part\s*Material|Plastic\s*Raw\s*Material)[^A-Za-z0-9]{0,80}(.{0,80})",
        txt, re.I
    )
    if label_match:
        label_section = label_match.group(2)

    for token in material_tokens:
        if re.search(token, label_section, re.I):
            return token.upper()

    for token in material_tokens:
        if re.search(token, txt, re.I):
            return token.upper()

    return ""

# ====================================================
# HYDRAULIC EJECTION CYLINDERS (NEW)
# ====================================================
def extract_hydraulic_ejection_cylinders(full_text: str) -> int:
    """
    Robust extraction of hydraulic ejection cylinder count.
    Existing logic is preserved.
    New logic is APPENDED at the end.
    """
    import re

    if not full_text:
        return 0

    lines = [l.strip() for l in full_text.splitlines() if l.strip()]

    for i, line in enumerate(lines):
        l = line.lower()

        # ✅ EXISTING RULE 1: HYD (2 NOS ...)
        m = re.search(r'\b(hyd|hydraulic)\b.*?\(\s*(\d+)\s*nos?\b', l, re.I)
        if m:
            return int(m.group(2))

        # ✅ EXISTING RULE 2: combine current + next line
        combined = l
        if i + 1 < len(lines):
            combined = l + " " + lines[i + 1].lower()

        # ✅ EXISTING RULE 3: Hydraulic ejection with X cylinders
        m = re.search(
            r'hydraulic\s+ejection\s+with\s+(\d+)\s+cylinders?',
            combined,
            re.I
        )
        if m:
            return int(m.group(1))

        # ✅ EXISTING RULE 4: ejection ... X cylinders
        if 'eject' in combined:
            m = re.search(r'\b(\d+)\s+cylinders?\b', combined)
            if m:
                return int(m.group(1))

    # =====================================================
    # ✅ APPENDED LOGIC (NEW — does not affect earlier code)
    # =====================================================
    for line in lines:
        l = line.lower()

        has_hyd = bool(re.search(r'\b(hydraulic|hyd\w*)\b', l))
        has_eje = bool(re.search(r'\b(ejection|eject|eje\w*)\b', l))
        has_cyl = bool(re.search(r'\b(cylinder|cylinders|cyl\w*)\b', l))

        if has_hyd and has_eje and has_cyl:
            num = re.search(r'\b(\d+)\b', l)
            if num:
                return int(num.group(1))

    return 0

def extract_slider_hydraulic_cylinders(full_text: str) -> int:
    """
    Extract number of hydraulic cylinders used for sliders.
    Handles lines like:
      - No. of Sliders 4 Nos-Hydraulic
      - No of Slider : 4 Nos HYD
    """
    import re

    if not full_text:
        return 0

    for line in full_text.splitlines():
        l = line.strip().lower()

        # Must mention slider(s)
        if not re.search(r'\bno\.?\s*of\s*sliders?\b', l):
            continue

        # Must mention hydraulic
        if not re.search(r'\b(hyd|hydraulic)\b', l):
            continue

        # Pick the first valid digit (slider count)
        m = re.search(r'\b(\d+)\b', l)
        if m:
            return int(m.group(1))

    return 0


# ====================================================
# BUILD JSON -- uses parse_core_cavity() and enforces defaults
# ====================================================
def build_json(full_text: str, pdf_pages, image_opts: Dict[str, Any], pdf_path_name, debug: bool = False) -> Dict[str, Any]:
    lines = full_text.splitlines()

    # PROJECT & PART NAME
    # ====================================================
    # PROJECT & PART NAME (FINAL – SAME VALUE FOR BOTH)
    # ====================================================
    project_name = ""
    part_name = ""

    for i, line in enumerate(lines):
        line_clean = line.strip()

        combo_match = re.search(
            r'Project\s*Name\s*&\s*Part\s*Name\s*[:\-]?\s*(.+)',
            line_clean,
            re.IGNORECASE
        )

        if combo_match:
            value = combo_match.group(1).strip()

            # Remove Date / Rev if present in SAME line
            split_same = re.split(
                r'\bDate\b|\bRev\b|\bRevision\b',
                value,
                flags=re.IGNORECASE
            )

            combined_value = split_same[0].strip()

            # If Date NOT present, read NEXT line
            if len(split_same) == 1 and i + 1 < len(lines):
                next_line = lines[i + 1].strip()

                split_next = re.split(
                    r'\bDate\b|\bRev\b|\bRevision\b',
                    next_line,
                    flags=re.IGNORECASE
                )

                combined_value = combined_value + " " + split_next[0].strip()

            # Cleanup & OCR fixes
            combined_value = re.sub(r'[^A-Za-z0-9]+', ' ', combined_value)#re.sub(r'\s+', ' ', combined_value)
            combined_value = re.sub(r'\s+', ' ', combined_value).strip()
            combined_value = combined_value.replace(" _", "_").replace("_ ", "_")
            combined_value = combined_value.replace("FRON T", "FRONT")
            #combined_value = combined_value.replace("FRT LH RH", "FRTLHRH")
            combined_value = combined_value.strip()

            # SAME VALUE FOR BOTH
            part_name = '_'.join(combined_value.replace('_',' ').split()[1:])
            project_name  = combined_value.replace('_',' ').split()[0]

            break

    if not project_name:
        for line in lines:
            proj_match = re.search(r'^Project\s*Name\s*[:\-]?\s*(.+)', line.strip(), re.I)
            if proj_match:
                project_name = re.split(r'\bDate\b|\bRev\b|\bRevision\b|\bLead Time\b|\bMFG\b', proj_match.group(1), flags=re.I)[0].strip()
                break
    print(project_name.replace('_'," ").split(' '))

    if not part_name:
        for line in lines:
            part_match = re.search(r'^Part\s*Name\s*[:\-]?\s*(.+)', line.strip(), re.I)
            if part_match:
                part_name = re.split(r'\bDate\b|\bRev\b|\bRevision\b|\bLead Time\b|\bMFG\b', part_match.group(1), flags=re.I)[0].strip()
                break

    tonnage = extract_tonnage(full_text)
    part_size = extract_part_size(full_text)
    lifter_sizes = extract_lifter_sizes(full_text)

    mold_details: Dict[str, Any] = {}
    part_details: Dict[str, Any] = {}

    
    # NEW: Part Ejection – Hydraulic cylinders count
    part_details["No.of Hydraulic Cylinder for Ejection"] = extract_hydraulic_ejection_cylinders(full_text)

    core_grade, cavity_grade = extract_core_cavity_material_grades(full_text)
    part_details["Core_material_grade"] = core_grade
    part_details["Cavity_material_grade"] = cavity_grade

    _sliders_grade, _lifters_grade = extract_slider_lifter_material_grades(full_text)

    ejection_type = ""
    if re.search(r'double\s*eject(?:ion)?', full_text, re.I):
        ejection_type = "Double"
    elif re.search(r'\bpart\s*ejection\b|\bejection\b', full_text, re.I):
        ejection_type = "Single"
    mold_details["Ejection Type"] = ejection_type

    mold_details["No of Eye Bolt"] = ""
    mold_details["Type Of Eye Bolt"] = ""
    eyebolt_matches = re.findall(r'(?:eye\s*bolt.*?(?:qty|quantity|nos?)\s*[:=\-]?\s*(\d+))', full_text, re.I)
    if eyebolt_matches:
        mold_details["No of Eye Bolt"] = int(eyebolt_matches[0])
    if mold_details.get("No of Eye Bolt") in ["", 0, None]:
        tonnage_match = re.search(
            r'Injection\s*Mould(?:ing)?\s*M/C\s*(\d{2,4})\s*T',
            full_text,
            re.I
        )

        if not tonnage_match:
            tonnage_match = re.search(
                r'''(?isx)
                \b(\d{2,4})\s*T\b
                (?:(?!\b\d{2,4}\s*T\b).)*?
                \beye[\s\-]*bolts?\b|eyebolts?\b
                ''',
                full_text
            )

        if not tonnage_match:
            tonnage_match = re.search(
                r'''(?isx)
                \b(\d{2,4})\s*(?:ton(?:ne)?s?|t)\b
                (?:(?!\b\d{2,4}\s*(?:ton(?:ne)?s?|t)\b).)*?
                \beye[\s\-]*bolts?\b|eyebolts?\b
                ''',
                full_text
            )

        if tonnage_match:
            machine_tonnage = int(tonnage_match.group(1))
            
            if machine_tonnage < 500:
                mold_details["No of Eye Bolt"] = 2
            elif 500 <= machine_tonnage <= 1500:
                mold_details["No of Eye Bolt"] = 4
            else:
                mold_details["No of Eye Bolt"] = 4
        else:
            mold_details["No of Eye Bolt"] = 2
    
    explicit_type = None
    for i, line in enumerate(lines):
        if re.search(r'\beye\s*bolt', line, re.I):
            block = " ".join(lines[i:i+3]).lower()
            if re.search(r'to\s*be\s*considered|std\.|standard|should|850t|above|for\s*small|considered|note:|guideline', block):
                continue
            if not re.search(r':\s*\d+\s*(nos?|no)', block):
                continue
            if re.search(r'\bnormal\s*\(std\)', block):
                explicit_type = "Normal (STD)"; break
            if re.search(r'\blocal\b', block):
                explicit_type = "Local"; break
            if re.search(r'\bswivel\b', block):
                explicit_type = "Swivel"; break
    mold_details["Type Of Eye Bolt"] = explicit_type if explicit_type else ("Local" if (mold_details.get("No of Eye Bolt") or 0) <= 2 else "Swivel")

    part_finish_raw = ""
    pf_block = ""
    for i, line in enumerate(lines):
        if re.search(r'Part\s*Finish(?:\s*&\s*(?:Colour|Color))?', line, re.I):
            pf_block = " ".join(lines[i:i+3])
            break
    m_pf = re.search(
        r'Part\s*Finish(?:\s*&\s*(?:Colour|Color))?\s*[:\-]?\s*([^\n]+?)\s*(?:No\.?of|Injection|Number of HRS|Texture|Mould Life|Description|$)',
        pf_block, re.I
    )
    if m_pf:
        part_finish_raw = m_pf.group(1).strip()
        part_finish_raw = re.sub(r'^\s*(?:&\s*)?(?:colour|color)\s*[:\-]?\s*', '', part_finish_raw, flags=re.I)

    def _extract_texture_spec_yes_no(ft: str) -> str:
        m1 = re.search(r'Texture\s*spec[^\: \n]*[:\-]?\s*(Yes|No)\b', ft, re.I)
        if m1: return m1.group(1).title()
        m2 = re.search(r'Texture\s*spec[^\: \n]*[:\-]?\s*(NA|N\.?A\.?)\b', ft, re.I)
        if m2: return "NA"
        m3 = re.search(r'(Texture\s*spec[^\n]*?)\n\s*(Yes|No|NA)\b', ft, re.I)
        if m3:
            val = m3.group(2).upper()
            return "Yes" if val == "YES" else ("No" if val == "NO" else "NA")
        return ""

    if not part_finish_raw:
        tex_val = _extract_texture_spec_yes_no(full_text)
        if tex_val:
            part_finish_raw = tex_val
    if not part_finish_raw:
        if re.search(r'\bMIC\b', full_text, re.I):
            part_finish_raw = "MIC"
        elif re.search(r'\bpaint(ed|ing)?\b', full_text, re.I):
            m_color = re.search(r'(?:colour|color)?\s*([A-Za-z ]+)\s*paint(?:ed|ing)?', full_text, re.I)
            part_finish_raw = (m_color.group(1).strip() + " Painting") if m_color else "Painted"
        elif re.search(r'mirror|high\s*gloss|chrome', full_text, re.I):
            part_finish_raw = "Mirror / High Gloss"
        elif re.search(r'texture\s*spec|grain', full_text, re.I):
            if re.search(r'texture\s*spec(?!.*\bNA\b)', full_text, re.I):
                part_finish_raw = "Grain"
        elif re.search(r'\bna\b|blank|tbd|tdb|no\s*\(mic\)', full_text, re.I):
            m_flag = re.search(r'\b(na|tbd|tdb|no\s*\(mic\))\b', full_text, re.I)
            part_finish_raw = m_flag.group(1) if m_flag else "NA"

    part_details["Part Finish Raw"] = part_finish_raw
    part_details["Part Finish"] = normalize_part_finish(part_finish_raw)

    part_details["Part Material (Grade)"] = extract_part_material(full_text)
    material_section = ""
    for i, line in enumerate(lines):
        if re.search(r'Part\s*Material|Plastic\s*Raw\s*Material', line, re.I):
            start = i
            end = min(i + 6, len(lines))
            material_section = " ".join(lines[start:end])
            break
    if material_section:
        material_pattern = r'''
            \b(
                (PP|PA|ABS|PC|POM|PET|PBT|NYLON|TPE)
                \d{0,2}
                (?:\s*[+-/]\s*[A-Z]{2,6}\d{0,2})*
                (?:\s*(GF|LGF|T|TD|MD)\d{1,2})?
            )\b
        '''
        match = re.search(material_pattern, material_section, re.I | re.VERBOSE)
        if match:
            part_details["Part Material (Grade)"] = match.group(0).replace(" ", " ").strip().upper()
    if part_details["Part Material (Grade)"] == "" and material_section:
        fallback_pattern = r'\b(PP|PA|ABS|PC|POM|PET|PBT|NYLON|TPE)\b'
        fallback_match = re.search(fallback_pattern, material_section, re.I)
        if fallback_match:
            part_details["Part Material (Grade)"] = fallback_match.group(0).upper()

    mold_details["No. of Trials"] = ""
    trial_match = re.search(r'with\s*in\s*(\d+)\s*trials', full_text, re.I)
    if trial_match:
        mold_details["No. of Trials"] = int(trial_match.group(1))
    else:
        trial_match2 = re.search(r'(\d+)\s*trials?', full_text, re.I)
        if trial_match2:
            mold_details["No. of Trials"] = int(trial_match2.group(1))

    mold_details["No of Parts"] = ""
    pilot_match = re.search(r'Pilot\s*(?:Production\s*Run)?.*?qty\.?\s*(\d+)\s*nos', full_text, re.I | re.S)
    if pilot_match:
        mold_details["No of Parts"] = int(pilot_match.group(1))
    else:
        fallback_match = re.search(r'to\s*be\s*conducted\s*for\s*(\d+)(?:\s*[-–]\s*\d+)?\s*(?:nos|pieces|parts)', full_text, re.I)
        if fallback_match:
            mold_details["No of Parts"] = int(fallback_match.group(1))
        else:
            trial_match = re.search(r'(?:trial|production).*?(\d+)\s*(?:nos|pieces|parts)', full_text, re.I)
            if trial_match:
                mold_details["No of Parts"] = int(trial_match.group(1))
            else:
                mold_details["No of Parts"] = ""

    #part_details["No of Cylinders For Sliders"] = 0
    # --- No of Cylinders For Sliders ---
    part_details["No of Cylinders For Sliders"] = extract_slider_hydraulic_cylinders(full_text)

    if part_details["No of Cylinders For Sliders"] == 0:
        for i, line in enumerate(lines):
            if re.search(r'cylinder.*slider|slider.*cylinder', line, re.I):
                num_match = re.search(r'\b(\d+)\b', line)
                if num_match:
                    part_details["No of Cylinders For Sliders"] = int(num_match.group(1))
                    break
    if part_details["No of Cylinders For Sliders"] == 0:
        m = re.search(r'\bHydraulic\b[^.\n]{0,40}\b(\d{1,2})\s*nos', full_text, re.I)
        if m:
            part_details["No of Cylinders For Sliders"] = int(m.group(1))
    if part_details["No of Cylinders For Sliders"] == 0:
        m2 = re.search(r'\bHyd\b[-\s–_]?0?(\d{1,2})\b', full_text, re.I)
        if m2:
            part_details["No of Cylinders For Sliders"] = int(m2.group(1))

    mold_details["Gas Spring"] = "Yes" if re.search(r'gas\s*spring', full_text, re.I) else "No"

    part_details["Mold Size"] = ""
    fallback = re.search(
        r'Mould\s*size.*?(\d{3,4})\s*(?:[xX×*]\s*)?(\d{3,4})\s*(?:[xX×*]\s*)?(\d{3,4})',
        full_text, re.I | re.S
    )
    if fallback:
        l, w, h = fallback.groups()
        part_details["Mold Size"] = f"{l} x {w} x {h}"

    mold_details["Mold weight (KG)"] = parse_mould_weight_to_kg(full_text)

    hrs_keywords = ["SVG", "HOT SPRUE", "Open type", "Manifold", "Hot"]
    crs_keywords = ["Cold", "Cold sprue", "NA", "cold runner"]
    blank_keywords = ["TDB", "Blank"]
    detected_mold_type = ""

    for line in lines:
        line_upper = line.upper()
        if any(kw.upper() in line_upper for kw in hrs_keywords):
            detected_mold_type = "HRS"; break
        elif any(kw.upper() in line_upper for kw in crs_keywords) and detected_mold_type == "":
            detected_mold_type = "CRS"
        elif any(kw.upper() in line_upper for kw in blank_keywords) and detected_mold_type == "":  # <--- FIXED TYPO HERE
            detected_mold_type = "Blank"
    mold_details["Mold Type"] = detected_mold_type

    text_lower = full_text.lower()
    mold_type_upper = mold_details.get("Mold Type", "").upper()
    if "CRS" in mold_type_upper:
        rs_type = "Cold Sprue"
    elif re.search(r'hot\s*(sprue|bush)|\bhot\b', text_lower):
        rs_type = "Hot Sprue"    
    elif re.search(r'\bsvg\b|\bsvg\s*type\b', text_lower):
        rs_type = "Seq Moulds/Per Drop"
    elif re.search(r'open\s*type', text_lower):
        rs_type = "Multiple Drops Open Type"
    elif re.search(r'l\s*manifold|1\s*drop\s*manifold|1\s*drop\s*open\s*type', text_lower):
        rs_type = "Single Drop L Manifold"
    
    else:
        rs_type = ""
    mold_details["RS Type"] = rs_type

    mold_details["No of Drops"] = 0
    for line in lines:
        clean_line = line.replace("\u200b", " ").strip()
        drops_match = re.search(r'(?:No\.?\s*of\s*)?(Drops|Drop|HRS\s*Drops)\s*[:=\-]?\s*([\d\+\s]+)', clean_line, re.I)
        if drops_match:
            drops_text = drops_match.group(2).replace(" ", "")
            if "+" in drops_text:
                mold_details["No of Drops"] = sum(int(x) for x in drops_text.split("+") if x.isdigit())
            elif drops_text.isdigit():
                mold_details["No of Drops"] = int(drops_text)

    mold_details["Cost of Spares"] = ""
    found = False
    for line in lines:
        l = line.lower()
        percent_match = re.search(r'(\d+\.?\d*)\s*%\s*of\s*tool\s*cost', l)
        if percent_match:
            mold_details["Cost of Spares"] = f"{percent_match.group(1)}%"; found = True; break
    if not found:
        for line in lines:
            l = line.lower()
            if "spare" in l or "spares" in l:
                value_match = re.search(r'([\d.,]+)\s*(k|lacs?|lakhs?|cr|crores?|rs\.?|inr)', l)
                alt_match = re.search(r'(inr|rs\.?)\s*([\d.,]+)\s*(k|lacs?|lakhs?|cr|crores?)?', l)
                amount = None; unit = None
                try:
                    if value_match:
                        amount = float(value_match.group(1).replace(",", "")); unit = value_match.group(2).lower()
                    elif alt_match:
                        amount = float(alt_match.group(2).replace(",", "")); unit = (alt_match.group(3) or "inr").lower()
                except:
                    amount = None; unit = None
                if amount is not None:
                    if unit == "k":
                        mold_details["Cost of Spares"] = int(amount * 1_000)
                    elif "lac" in unit:
                        mold_details["Cost of Spares"] = int(amount * 100_000)
                    elif "cr" in unit:
                        mold_details["Cost of Spares"] = int(amount * 10_000_000)
                    else:
                        mold_details["Cost of Spares"] = int(amount)
                    found = True; break
    if not found:
        mold_details["Cost of Spares"] = "1%"

    dims = parse_core_cavity(full_text)
    # ====================================================
    # ✅ SAFE ADDITIVE PATCH FOR CORE / CAVITY SIZES
    # Runs ONLY when existing parser failed (empty values)
    # Does NOT affect old working PDFs
    # ====================================================

    try:
        if (
            dims.get("Core Block", "") == ""
            and dims.get("Cavity Block", "") == ""
            and "core, cavity & other sizes" in full_text.lower()
        ):

            text_norm = re.sub(r"\s+", " ", full_text)

            def _find_dims(label_regex):
                m = re.search(
                    rf'{label_regex}\s*[:\-]\s*(\d{{3,5}})\s*[xX]\s*(\d{{3,5}})\s*[xX]\s*(\d{{3,5}})',
                    text_norm,
                    re.IGNORECASE
                )
                return f"{m.group(1)}x{m.group(2)}x{m.group(3)}" if m else ""

            # Fill ONLY if missing
            if not dims.get("Cavity Block"):
                dims["Cavity Block"] = _find_dims(r"Cavity")

            if not dims.get("Core Block"):
                dims["Core Block"] = _find_dims(r"Core\s*(?:hsg|housing)?")

            if not dims.get("Core Insert"):
                dims["Core Insert"] = _find_dims(r"Core\s*Insert")

            # Intentionally DO NOT set Cavity Insert
            # → default 1X1X1 will apply if missing

    except Exception:
        pass




    text_clean = re.sub(r"\s+", " ", full_text.replace("\n", " "))
    core_qty_match = re.search(r'core\s*(?:qty|quantity|nos?|no\.?)\s*[:\-]?\s*(\d+)', text_clean, re.IGNORECASE)
    cavity_qty_match = re.search(r'cavity\s*(?:qty|quantity|nos?|no\.?)\s*[:\-]?\s*(\d+)', text_clean, re.IGNORECASE)
    core_insert_qty_match = re.search(r'core\s*(?:insert|ins\.?)\s*(?:qty|quantity|nos?|no\.?)\s*[:\-]?\s*(\d+)', text_clean, re.IGNORECASE)
    cavity_insert_qty_match= re.search(r'cavity\s*(?:insert|ins\.?)\s*(?:qty|quantity|nos?|no\.?)\s*[:\-]?\s*(\d+)', text_clean, re.IGNORECASE)

    core_cavity: Dict[str, Any] = {
        "Core Block": dims.get("Core Block", ""),
        "Cavity Block": dims.get("Cavity Block", ""),
        "Core Insert": dims.get("Core Insert", ""),
        "Cavity Insert": dims.get("Cavity Insert", ""),
        "Core Quantity": int(core_qty_match.group(1)) if core_qty_match else 0,
        "Cavity Quantity": int(cavity_qty_match.group(1)) if cavity_qty_match else 0,
        "Core Insert Quantity": int(core_insert_qty_match.group(1)) if core_insert_qty_match else 0,
        "Cavity Insert Quantity": int(cavity_insert_qty_match.group(1)) if cavity_insert_qty_match else 0,
    }

    if core_cavity.get("Core Block") and not core_cavity.get("Cavity Block"):
        core_cavity["Cavity Block"] = core_cavity["Core Block"]
    if core_cavity.get("Cavity Block") and not core_cavity.get("Core Block"):
        core_cavity["Core Block"] = core_cavity["Cavity Block"]

    _size_defaults = {
        "Core Block": "1X1X1",
        "Cavity Block": "1X1X1",
        "Core Insert": "1X1X1",
        "Cavity Insert": "1X1X1",
    }
    for k, v in _size_defaults.items():
        if not str(core_cavity.get(k, "")).strip():
            core_cavity[k] = v

    _qty_keys = [
        "Core Quantity",
        "Cavity Quantity",
        "Core Insert Quantity",
        "Cavity Insert Quantity",
    ]
    for k in _qty_keys:
        try:
            val = int(core_cavity.get(k) or 0)
        except Exception:
            val = 0
        if val <= 0:
            core_cavity[k] = 1

    images = []
    #print('pdf_name :',pdf_path_name)
    if image_opts.get("enabled", True):
        try:
            images = extract_mould_images_backup(
                pdf_pages=pdf_pages,
                part_name=pdf_path_name,#part_name,#+pdf_path.name, #added by surendra
                image_folder=Path(image_opts["folder"]),
                resolution=image_opts.get("resolution", 75),
                debug=debug
            )
            
            
        except Exception as e:
            if debug:
                print(f"[DEBUG] backup-style image extraction error: {e}")
            images = []
    part_details["Mould Image"] = images

    # Clean part name: keep only alphanumeric and spaces, strip outer spaces, replace inner space(s) with single underscore

    project_name = re.sub(r'[^A-Za-z0-9 ]+', '', project_name)
    project_name = re.sub(r'\s+', '_', project_name.strip()).strip('_')
    cavity_orientation = 'Center Multicavity' if extract_no_of_cavity(lines)>1 else 'Center Single Cavity'
    part_name = re.sub(r'[^A-Za-z0-9]+', ' ', part_name)
    part_name = re.sub(r'\s+', ' ', part_name).strip()
    part_name = part_name.replace(' ','_')


    data = {
        "Project Name": project_name,
        "Part Name": part_name,
        "Tonnage": tonnage,
        "Estimation Type": "VOB SBC",
        "Part Weight (KG)": 0.01,
        "Supplier Class": "class B",
        "Mold Mfg Location": "India",
        "Date": extract_date(full_text),
        "Part Accuracy" :"Moderate Precision",
        "Part Size": part_size,
        "Parting line Requirement": "Core+ Cavity",
        **mold_details,
        **part_details,
        "No of Lifters": extract_lifters(full_text),
        "Lifter Sizes": extract_lifter_sizes(full_text),
        "No of Sliders": extract_sliders(full_text),
        "Cavity Orientation": cavity_orientation,
        "No of Cavity": extract_no_of_cavity(lines),
        "Core/Cavity Sizes": core_cavity,
        "Sliders_material_grade": _sliders_grade,
        "Lifters_material_grade": _lifters_grade,
    }
    # ====================================================
    # ✅ SAFE ADDITIVE PATCH – LIFTERS & SLIDERS COUNT
    # Handles: "No.of Lifters,Sliders  L = 14  S = 09"
    # Does NOT affect existing PDFs or logic
    # ====================================================

    try:
        if (
            data.get("No of Lifters", 0) == 0
            and data.get("No of Sliders", 0) == 0
            and re.search(r'\bL\s*=\s*\d+', full_text, re.IGNORECASE)
            and re.search(r'\bS\s*=\s*\d+', full_text, re.IGNORECASE)
        ):
            m = re.search(
                r'\bL\s*=\s*(\d+)\b.*?\bS\s*=\s*(\d+)\b',
                full_text,
                re.IGNORECASE | re.DOTALL
            )
            if m:
                data["No of Lifters"] = int(m.group(1))
                data["No of Sliders"] = int(m.group(2))
    except Exception:
        pass
    if debug:
        print("[DEBUG] Final Core/Cavity object:", json.dumps(core_cavity, indent=2))
    return data

# ====================================================
# MAIN
# ====================================================
def main():
    parser = argparse.ArgumentParser(description="Extract mould JSON from PDFs.")
    parser.add_argument("--folder", default=PDF_FOLDER_DEFAULT, help="Folder containing PDFs")
    parser.add_argument("--out", default=OUTPUT_FOLDER_DEFAULT, help="Folder to save JSON files")
    parser.add_argument("--img", default=IMAGE_FOLDER_DEFAULT, help="Folder to save images")
    parser.add_argument("--input", help="Process a single PDF file (overrides --folder)")
    parser.add_argument("--max-images", type=int, default=4, help="(kept for compatibility; unused by backup mode)")
    parser.add_argument("--min-px", nargs=2, type=int, metavar=("MIN_W", "MIN_H"), default=[120, 120],
                        help="(kept for compatibility; unused by backup mode)")
    parser.add_argument("--no-base64", action="store_true", help="(kept for compatibility; backup mode always embeds)")
    parser.add_argument("--no-images", action="store_true", help="Skip image extraction entirely")
    parser.add_argument("--resolution", type=int, default=150, help="Image extraction resolution (DPI)")
    parser.add_argument("--debug", action="store_true", help="Print debug info")
    parser.add_argument("--left-only", action="store_true", help="(deprecated) no effect in backup mode")
    parser.add_argument("--no-left", action="store_true", help="(deprecated) no effect in backup mode")
    args = parser.parse_args()

    pdf_dir = Path(args.folder)
    out_dir = Path(args.out)
    img_dir = Path(args.img)

    out_dir.mkdir(parents=True, exist_ok=True)
    img_dir.mkdir(parents=True, exist_ok=True)

    image_opts = {
        "enabled": (not args.no_images),
        "folder": str(img_dir),
        "resolution": args.resolution,
    }

    to_process: List[Path] = []
    if args.input:
        one = Path(args.input)
        if one.exists() and one.suffix.lower() == ".pdf":
            to_process = [one]
        else:
            print(f"❌ Input file not found or not a PDF: {one}")
            return
    else:
        if not pdf_dir.exists():
            print(f"❌ Folder not found: {pdf_dir}")
            return
        for filename in os.listdir(pdf_dir):
            if filename.lower().endswith(".pdf"):
                to_process.append(pdf_dir / filename)

    for pdf_path in to_process:
        print(f"🚀 Reading PDF: {pdf_path.name} ...")
        try:
            reader = PdfReader(str(pdf_path))
        except Exception as e:
            print(f"❌ Failed to open with PyPDF2: {e}")
            continue

        full_text = ""
        pages2 = list(reader.pages)
        for page in pages2:
            try:
                txt = page.extract_text() or ""
            except Exception:
                txt = ""
            if txt:
                full_text += txt + "\n"
        #print(full_text) 
        #print(f'pdf_name: {pdf_path.name}')    
        pdf_path_name = pdf_path.name.split('.')[0]
        try:
            with pdfplumber.open(str(pdf_path)) as pdf:
                json_data = build_json(full_text, pdf.pages, image_opts=image_opts, debug=args.debug, pdf_path_name=pdf_path_name)
        except Exception as e:
            print(f"⚠️ pdfplumber open failed ({e}); proceeding without images.")
            json_data = build_json(full_text, pdf_pages=[], image_opts={"enabled": False, "folder": str(img_dir)}, debug=args.debug, pdf_path_name=pdf_path_name)

        json_filename = out_dir / (pdf_path.stem + ".json")
        with open(json_filename, "w", encoding="utf-8") as f:
            json.dump(json_data, f, indent=4, ensure_ascii=False)
        print(f"✅ JSON created: {json_filename}")

if __name__ == "__main__":
    import sys
    try:
        main()
    except SystemExit:
        raise
    except KeyboardInterrupt:
        print("\n⚠️ Processing interrupted by user", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        import traceback
        print(f"❌ CRITICAL ERROR in pdf_to_json: {str(e)}", file=sys.stderr)
        print(traceback.format_exc(), file=sys.stderr)
        sys.exit(1)