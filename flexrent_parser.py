"""
flexrent_parser.py
Universal Nigerian Bank Statement Parser (v5.2 - Opay Strict Mode)
Features:
  - Opay Mode: Disables Position Parser to prevent duplicates
  - Supports '25 Oct 2024' dates and '--' zero values
  - Cross-Page Memory for UBA/Access
"""
import re
from collections import defaultdict, OrderedDict
import pdfplumber
from pdfminer.pdfdocument import PDFPasswordIncorrect

# --- CONFIGURATION ---
MONEY_RE = re.compile(r'(?<!\d)([0-9]{1,3}(?:[, ]\d{3})*\.\d{2})(?!\d)')

# Date Regex: Matches "25-Oct-2024" AND "25 Oct 2024"
LINE_DATE_RE = re.compile(r'(?:\d{2}[-/][A-Za-z0-9]{3}[-/]\d{2,4})|(?:\d{2}\s[A-Za-z]{3}\s\d{4})')

SUMMARY_DEPOSITS_RE = re.compile(r'(?:total deposits?|total credits?|total inflows?|credit turnover)[:\s\n]*([0-9,]+\.\d{2})|Total Credit Credit Count[\s\n\S]*?₦?[0-9,.]+\s+₦?([0-9,]+\.\d{2})', re.IGNORECASE)

# Access/Opay Patterns
MONEY_OR_DASH_RE = r'(?:[0-9,]+\.\d{2}|-|--)' # Added '--' for Opay
ACCESS_STD_PATTERN = re.compile(fr'({LINE_DATE_RE.pattern})\s+({LINE_DATE_RE.pattern})\s+(.*?)\s+({MONEY_OR_DASH_RE})\s+({MONEY_OR_DASH_RE})\s+({MONEY_OR_DASH_RE})\s*$')
ACCESS_COMPACT_PATTERN = re.compile(fr'({LINE_DATE_RE.pattern})\s+({LINE_DATE_RE.pattern})\s+({MONEY_OR_DASH_RE})\s+({MONEY_OR_DASH_RE})\s+({MONEY_OR_DASH_RE})\s*$')

SALARY_KEYWORDS = [
    "salary", "payroll", "net pay", "staff id", "ippis", "fgn", 
    "remita payment", "allowance", "stipend", "month end", "upkeep"
]

# Opay Internal Transfer Keywords (To ignore self-transfers if needed)
SELF_TRANSFER_KEYS = ["owealth", "ofixed", "saving", "to self"]

def safe_float(s):
    if not s: return 0.0
    s_clean = str(s).strip()
    if s_clean in ['-', '--']: return 0.0
    try:
        clean = re.sub(r'[^\d.-]', '', s_clean.replace(' ', ''))
        val = float(clean)
        if abs(val) > 10_000_000_000: return 0.0
        return val
    except:
        return 0.0

def detect_salary_income(transactions):
    salary_txns = []
    total_salary = 0.0
    for tx in transactions:
        credit = tx.get("credit")
        desc = (tx.get("description") or "").lower()
        if credit and credit > 0:
            if any(key in desc for key in SALARY_KEYWORDS):
                salary_txns.append(tx)
                total_salary += credit
    return total_salary, salary_txns

def extract_summary(text):
    clean_text = text.replace('₦', '')
    match = SUMMARY_DEPOSITS_RE.search(clean_text)
    if match:
        return safe_float(match.group(1) or match.group(2))
    return None

# --- POSITION PARSER (Skipped for Opay) ---
def round_key(v, precision=3.0): return round(v / precision) * precision
def group_words_to_rows(words, y_tol=3.0):
    rows = defaultdict(list)
    for w in words:
        key = round_key(w['top'], precision=y_tol)
        rows[key].append(w)
    ordered = OrderedDict()
    for y in sorted(rows.keys()): ordered[y] = sorted(rows[y], key=lambda x: x['x0'])
    return ordered
def infer_header_row(rows):
    for y, words in rows.items():
        line = " ".join([w['text'] for w in words]).lower()
        if "date" in line and ("credit" in line or "cr" in line): return y, words
    return None, None
def make_col_positions(header_words):
    col_positions = [w['x0'] for w in header_words]
    texts = [w['text'].strip().lower() for w in header_words]
    return col_positions, texts
def assign_row_to_cols(row_words, col_positions):
    assigned = {i: [] for i in range(len(col_positions))}
    for w in row_words:
        x = w['x0']
        diffs = [abs(x - cp) for cp in col_positions]
        idx = diffs.index(min(diffs))
        assigned[idx].append(w['text'])
    return [" ".join(assigned[i]).strip() for i in range(len(col_positions))]

def parse_position_page(page):
    words = page.extract_words(extra_attrs=["x0","x1","top","bottom"])
    if not words: return []
    rows = group_words_to_rows(words, y_tol=6.0) 
    header_y, header_words = infer_header_row(rows)
    if header_words is None: return []
    col_positions, header_texts = make_col_positions(header_words)
    col_map = []
    for h in header_texts:
        if "date" in h: col_map.append("date")
        elif "desc" in h: col_map.append("description")
        elif "debit" in h: col_map.append("debit")
        elif "credit" in h: col_map.append("credit")
        elif "balance" in h: col_map.append("balance")
        else: col_map.append("unknown")
    txs = []
    for y, row_words in rows.items():
        if y <= header_y: continue
        cols_text = assign_row_to_cols(row_words, col_positions)
        tx = {"date": None, "description": "", "debit": 0.0, "credit": 0.0, "balance": 0.0}
        for i, text in enumerate(cols_text):
            if i >= len(col_map): break
            ctype = col_map[i]
            if ctype == "date": tx["date"] = text
            elif ctype == "description": tx["description"] = text
            elif ctype in ["debit", "credit", "balance"]:
                if re.search(r'[A-Za-z]', text.replace(',','').replace('.','')): continue
                val = safe_float(text)
                if val is not None: tx[ctype] = val
        if tx["debit"] > 0 or tx["credit"] > 0: txs.append(tx)
    return txs

# --- LINE PARSER (Opay Logic) ---

def parse_opay_line(line, prev_line_text):
    # Opay Regex: Date Time Date Amount -- Balance
    # Looks for "25 Oct 2024" and "Debit Credit Balance" pattern
    tokens = line.split()
    if not re.match(r'\d{2}\s[A-Za-z]{3}\s\d{4}', line): return None

    date_matches = LINE_DATE_RE.findall(line)
    if not date_matches: return None
    p_date = date_matches[0]

    # Capture 3 money columns (Debit, Credit, Balance)
    # Handles 1,000.00 or -- 
    money_pattern = re.compile(r'([0-9,]+\.\d{2}|--)\s+([0-9,]+\.\d{2}|--)\s+([0-9,]+\.\d{2})')
    money_match = money_pattern.search(line)
    
    if money_match:
        deb_str, cred_str, bal_str = money_match.groups()
        description = prev_line_text if prev_line_text else "Opay Transaction"
        return {
            "date": p_date, "description": description,
            "credit": safe_float(cred_str), "debit": safe_float(deb_str), "balance": safe_float(bal_str)
        }
    return None

def parse_lines_fallback(page_text, previous_balance=None):
    txs = []
    last_known_balance = previous_balance
    if not page_text: return txs, last_known_balance

    lines = page_text.split('\n')
    prev_line_text = "" 
    
    for line in lines:
        line = line.strip()
        
        # 1. Opay Check
        opay_tx = parse_opay_line(line, prev_line_text)
        if opay_tx:
            txs.append(opay_tx)
            prev_line_text = ""
            continue

        # 2. Access Bank Check
        std_match = ACCESS_STD_PATTERN.search(line)
        compact_match = ACCESS_COMPACT_PATTERN.search(line)
        
        if std_match:
            p_date, v_date, desc_part, deb_str, cred_str, bal_str = std_match.groups()
            bal = safe_float(bal_str)
            last_known_balance = bal
            txs.append({
                "date": p_date, "description": (prev_line_text + " " + desc_part).strip(),
                "credit": safe_float(cred_str), "debit": safe_float(deb_str), "balance": bal
            })
            prev_line_text = ""
            continue
        elif compact_match:
            p_date, v_date, deb_str, cred_str, bal_str = compact_match.groups()
            bal = safe_float(bal_str)
            last_known_balance = bal
            txs.append({
                "date": p_date, "description": prev_line_text or "Unknown",
                "credit": safe_float(cred_str), "debit": safe_float(deb_str), "balance": bal
            })
            prev_line_text = ""
            continue

        # 3. Generic Fallback
        money_matches = MONEY_RE.findall(line)
        vals = [safe_float(m) for m in money_matches if safe_float(m) is not None]
        
        if len(vals) >= 2:
            date_match = re.search(LINE_DATE_RE, line)
            if date_match:
                p_date = date_match.group(0)
                current_balance = vals[-1]
                amount = vals[-2]
                is_credit = False
                
                if last_known_balance is not None:
                    diff = round(current_balance - last_known_balance, 2)
                    if diff > 0: is_credit = True
                    elif diff < 0: is_credit = False
                
                if not is_credit and last_known_balance is None:
                     is_credit = "credit" in line.lower() or "deposit" in line.lower()

                last_known_balance = current_balance
                txs.append({
                    "date": p_date, "description": line, 
                    "credit": amount if is_credit else 0.0, "debit": 0.0 if is_credit else amount,
                    "balance": current_balance
                })
                prev_line_text = ""
                continue

        if not re.search(r'Page \d|Posted Date|Statement Period', line, re.IGNORECASE):
            prev_line_text = line

    return txs, last_known_balance

# --- MAIN ---
def parse_pdf(path, password=None):
    all_txs = []
    full_text = ""
    running_balance = None

    try:
        with pdfplumber.open(path, password=password) as pdf:
            # Check if Opay (to disable Position Parser)
            first_page = pdf.pages[0].extract_text() or ""
            is_opay = "opay" in first_page.lower() or "wallet" in first_page.lower()

            for page in pdf.pages:
                text = page.extract_text()
                full_text += (text or "") + "\n"
                
                txs = []
                # ONLY use Position Parser if NOT Opay
                if not is_opay:
                    txs = parse_position_page(page)
                
                if txs:
                    if txs and "balance" in txs[-1]: running_balance = txs[-1]["balance"]
                else:
                    txs, new_bal = parse_lines_fallback(text, running_balance)
                    running_balance = new_bal
                
                all_txs.extend(txs)
    except PDFPasswordIncorrect:
        raise PermissionError("PASSWORD_REQUIRED")

    # Strict Dedupe for Opay
    unique_txs = {f"{t['date']}_{t['credit']}_{t['balance']}": t for t in all_txs}.values()
    clean_txs = list(unique_txs)

    total_credit = sum(t['credit'] for t in clean_txs)
    total_debit = sum(t['debit'] for t in clean_txs)
    summary_deposit = extract_summary(full_text)
    salary_est, _ = detect_salary_income(clean_txs)

    totals = {
        "total_credit": total_credit, "total_debit": total_debit, 
        "summary_match": summary_deposit, "salary_estimate": salary_est
    }
    return clean_txs, totals