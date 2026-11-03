#!/usr/bin/env python3
"""
Intercompany Loan Arm's-Length + Covenant Checker  (Tresata take-home prototype)

Pipeline:  clause text --> [EXTRACTOR: LLM or stub] --> grounding check
           --> [DETERMINISTIC ENGINE: Decimal math, no LLM] --> status + audit record

Jurisdiction: US (IRC s.482, Treas. Reg. 1.482-2(a) safe-haven band 100%-130% of AFR,
USD loans only). ALL AFR VALUES AND ALL TEST CASES BELOW ARE SYNTHETIC.

Run:        python transfer_pricing_prototype.py
Optional:   OPENROUTER_API_KEY=... OPENROUTER_MODEL=<free model id> python ... --llm
            (LLM mode is UNTESTED here; the 10 results below use the offline stub extractor.)
No third-party packages required (Python 3.9+).
"""
import hashlib, json, os, re, sys
from decimal import Decimal as D, getcontext

getcontext().prec = 28

# ---------------------------------------------------------------- knowledge in DATA, not in the model
# SYNTHETIC AFR table, annual compounding, month 2026-10. Real values come from IRS Rev. Rulings.
AFR_TABLE = {"version": "SYNTHETIC-2026-10", "compounding": "annual",
             "short": D("0.0410"), "mid": D("0.0435"), "long": D("0.0460")}
BAND_LOW, BAND_HIGH = D("1.00"), D("1.30")
PERIODS = {"annually": 1, "semi-annually": 2, "quarterly": 4, "monthly": 12}

# ---------------------------------------------------------------- 1. EXTRACTION (probabilistic layer)
def stub_extract(text):
    """Offline stand-in for the LLM. Returns {field: (value, verbatim_quote)}.
    Deliberately simple, so its failure modes are visible in the tests."""
    out = {}
    def grab(name, pattern, conv):
        m = re.search(pattern, text, re.I)
        if m:
            out[name] = (conv(m), m.group(0))
    grab("currency", r"principal amount of (USD|EUR|GBP)", lambda m: m.group(1).upper())
    grab("fixed_rate", r"fixed rate of ([\d.]+)% per annum", lambda m: D(m.group(1)) / 100)
    grab("floating", r"\bSOFR\b[^.]*", lambda m: True)
    grab("compounding", r"compounded (annually|semi-annually|quarterly|monthly)", lambda m: m.group(1).lower())
    grab("term_years", r"term of (\d+) (years|months)",
         lambda m: D(m.group(1)) if m.group(2).lower() == "years" else D(m.group(1)) / 12)
    grab("max_leverage", r"Total Debt to (?:Adjusted )?EBITDA ratio shall not exceed (\d+(?:\.\d+)?)", lambda m: D(m.group(1)))
    grab("min_icr", r"Interest Coverage Ratio shall not be less than (\d+(?:\.\d+)?)", lambda m: D(m.group(1)))
    return out

def llm_extract(text):  # UNTESTED optional path
    import urllib.request
    prompt = ("Extract from the loan clause. Return ONLY JSON: {field:{value,quote}} for fields "
              "currency, fixed_rate(decimal), floating(bool), compounding, term_years, max_leverage, min_icr. "
              "'quote' must be copied verbatim from the text. Omit fields not present. Do NOT calculate.\n\n" + text)
    req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps({"model": os.environ["OPENROUTER_MODEL"], "temperature": 0,
                         "messages": [{"role": "user", "content": prompt}]}).encode(),
        headers={"Authorization": "Bearer " + os.environ["OPENROUTER_API_KEY"], "Content-Type": "application/json"})
    raw = json.loads(urllib.request.urlopen(req, timeout=60).read())["choices"][0]["message"]["content"]
    data = json.loads(re.sub(r"^```(?:json)?|```$", "", raw.strip()).strip())
    conv = {"fixed_rate": D, "term_years": D, "max_leverage": D, "min_icr": D}
    return {k: (conv.get(k, lambda x: x)(str(v["value"]) if k in conv else v["value"]), v["quote"]) for k, v in data.items()}

# ---------------------------------------------------------------- 2. GROUNDING GATE (between layers)
def grounding_ok(ext, text):
    """Every extracted value must carry a quote that literally exists in the source."""
    bad = [k for k, (_, q) in ext.items() if q not in text]
    return (not bad), bad

# ---------------------------------------------------------------- 3. DETERMINISTIC ENGINE (no LLM here)
def term_bucket(years):
    return "short" if years <= 3 else ("mid" if years <= 9 else "long")

def effective_annual(nominal, compounding):
    m = PERIODS[compounding]
    return (1 + nominal / m) ** m - 1

def rate_test(ext):
    if ext.get("currency", ("",))[0] != "USD":
        return "ESCALATE", "Non-USD loan: s.1.482-2(a) safe haven unavailable; needs benchmarking study"
    if "floating" in ext:
        return "ESCALATE", "Floating-rate: fixed AFR comparison not applicable"
    if "fixed_rate" not in ext or "term_years" not in ext:
        return "NEEDS_REVIEW", "Missing rate or term (not extracted)"
    comp = ext.get("compounding", ("annually",))[0]
    eff = effective_annual(ext["fixed_rate"][0], comp)
    bucket = term_bucket(ext["term_years"][0])
    afr = AFR_TABLE[bucket]
    ratio = eff / afr
    detail = f"eff={eff:.6f} bucket={bucket} AFR={afr} ratio={ratio:.4f}"
    if ratio < BAND_LOW:  return "BELOW_SAFE_HARBOR", detail
    if ratio > BAND_HIGH: return "ABOVE_BAND_BENCHMARK", detail
    return "SAFE_HARBOR", detail

def covenant_test(ext, fin):
    if "max_leverage" not in ext and "min_icr" not in ext:
        return "N/A", "No covenant extracted"
    if fin["ebitda"] <= 0:
        return "ESCALATE", "Non-positive EBITDA"
    lev, icr = fin["debt"] / fin["ebitda"], fin["ebitda"] / fin["interest"]
    breaches = []
    if "max_leverage" in ext and lev > ext["max_leverage"][0]: breaches.append(f"leverage {lev:.2f}x > {ext['max_leverage'][0]}x")
    if "min_icr" in ext and icr < ext["min_icr"][0]:           breaches.append(f"ICR {icr:.2f}x < {ext['min_icr'][0]}x")
    return ("BREACH", "; ".join(breaches)) if breaches else ("OK", f"leverage {lev:.2f}x, ICR {icr:.2f}x")

def run_case(text, fin, extractor):
    ext = extractor(text)
    ok, bad = grounding_ok(ext, text)
    if not ok:
        rs, rd, cs, cd = "NEEDS_REVIEW", f"Ungrounded fields: {bad}", "NEEDS_REVIEW", ""
    else:
        rs, rd = rate_test(ext)
        cs, cd = covenant_test(ext, fin)
    return {"rate_status": rs, "rate_detail": rd, "covenant_status": cs, "covenant_detail": cd,
            "audit": {"text_sha256": hashlib.sha256(text.encode()).hexdigest()[:16],
                      "afr_table": AFR_TABLE["version"],
                      "extracted": {k: {"value": str(v[0]), "quote": v[1]} for k, v in ext.items()}}}

# ---------------------------------------------------------------- 4. TEN SYNTHETIC CASES (all made up)
def loan(cur="USD", rate="a fixed rate of 4.5% per annum", comp="compounded annually", term="a term of 2 years", cov=""):
    return (f"The Lender shall advance a principal amount of {cur} 25,000,000 to the Borrower. "
            f"The loan shall bear interest at {rate}, {comp}. The loan shall have {term}. {cov}")

COV = "The Total Debt to EBITDA ratio shall not exceed 4.0. The Interest Coverage Ratio shall not be less than 3.0."
FIN_OK = {"debt": D(20e6), "ebitda": D(8e6), "interest": D(1.5e6)}

CASES = [
 # id, description, text, financials, expected (rate_status, covenant_status), kind
 ("P1", "Boundary: exactly 130% of AFR, exactly 3y (short)", loan(rate="a fixed rate of 5.33% per annum", term="a term of 3 years", cov=COV), FIN_OK, ("SAFE_HARBOR", "OK")),
 ("P2", "Rate below band (3.00% vs 4.35% mid AFR)",           loan(rate="a fixed rate of 3.00% per annum", term="a term of 5 years", cov=COV), FIN_OK, ("BELOW_SAFE_HARBOR", "OK")),
 ("P3", "Rate above band -> benchmarking",                    loan(rate="a fixed rate of 6.50% per annum", term="a term of 10 years", cov=COV), FIN_OK, ("ABOVE_BAND_BENCHMARK", "OK")),
 ("P4", "Quarterly compounding lifts 4.05% nominal over AFR", loan(rate="a fixed rate of 4.05% per annum", comp="compounded quarterly", term="a term of 3 years", cov=COV), FIN_OK, ("SAFE_HARBOR", "OK")),
 ("P5", "EUR loan -> escalate (no safe haven)",               loan(cur="EUR", cov=COV), FIN_OK, ("ESCALATE", "OK")),
 ("P6", "Floating SOFR loan -> escalate",                     loan(rate="SOFR plus 2.00% per annum", term="a term of 4 years", cov=COV), FIN_OK, ("ESCALATE", "OK")),
 ("P7", "Safe-harbor rate but leverage covenant breach",      loan(rate="a fixed rate of 5.00% per annum", term="a term of 4 years", cov=COV), {"debt": D(60e6), "ebitda": D(10e6), "interest": D(3e6)}, ("SAFE_HARBOR", "BREACH")),
 # ---- designed to expose weaknesses (expected = what a careful human would conclude)
 ("F1", "Rate written in words (extractor blind spot)",
        loan(rate="a fixed rate of four and one-half percent per annum", cov=COV), FIN_OK, ("SAFE_HARBOR", "OK")),
 ("F2", "Initial 3y term + 5y borrower extension option (silent mis-bucket)",
        loan(rate="a fixed rate of 4.20% per annum", term="an initial term of 3 years, which the Borrower may extend by a further 5 years at its option", cov=COV), FIN_OK, ("BELOW_SAFE_HARBOR", "OK")),
 ("F3", "Covenant defined on *Adjusted* EBITDA; engine uses reported EBITDA",
        loan(rate="a fixed rate of 4.80% per annum", cov="The Total Debt to Adjusted EBITDA ratio shall not exceed 4.0."),
        {"debt": D(36e6), "ebitda": D(8e6), "interest": D(1.5e6), "adjusted_ebitda_NOT_USED": D(9.5e6)}, ("SAFE_HARBOR", "OK")),
]

def main():
    extractor = llm_extract if "--llm" in sys.argv else stub_extract
    results, passed = [], 0
    print(f"{'ID':<4}{'RESULT':<7}{'GOT (rate / covenant)':<46}EXPECTED")
    for cid, desc, text, fin, expected in CASES:
        r = run_case(text, fin, extractor)
        got = (r["rate_status"], r["covenant_status"])
        ok = got == expected; passed += ok
        print(f"{cid:<4}{'PASS' if ok else 'FAIL':<7}{got[0]+' / '+got[1]:<46}{expected[0]} / {expected[1]}   # {desc}")
        results.append({"id": cid, "description": desc, "pass": ok, "got": got, "expected": expected, **r})
    print(f"\n{passed}/{len(CASES)} matched expected outcome")
    with open("results.json", "w") as f:
        json.dump(results, f, indent=2, default=str)
    print("Audit records written to results.json")

if __name__ == "__main__":
    main()
