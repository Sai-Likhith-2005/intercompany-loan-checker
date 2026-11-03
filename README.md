# Intercompany Loan Arm's-Length & Covenant Checker

Prototype for the Tresata take-home. Jurisdiction: US (IRC §482,
Treas. Reg. §1.482-2(a) safe haven: 100%-130% of AFR, USD loans only).

**All data is synthetic. The AFR table is made up, not real IRS rates.**

## Idea
LLM reads the contract and returns each value with a verbatim quote.
Code verifies the quote exists in the source. A deterministic Decimal
engine does all the math. Anything unclear is escalated to a human.
The LLM never calculates.

## Run
    python transfer_pricing_prototype.py
Python 3.9+, no dependencies, no network. Writes results.json.

Optional LLM mode (untested):
    OPENROUTER_API_KEY=... OPENROUTER_MODEL=<free model id> python transfer_pricing_prototype.py --llm

## Model used
None for the reported results (offline regex stub standing in for the LLM).

## Tests: 10 synthetic cases, 7/10 match expected
P1-P7: boundary (exactly 130% / 3y), below band, above band, quarterly
compounding, EUR escalate, floating escalate, covenant breach.
F1: rate written in words -> NEEDS_REVIEW (safe fail)
F2: extension option ignored -> wrong bucket (silent fail, the dangerous one)
F3: covenant on Adjusted EBITDA, engine uses reported -> false BREACH

## Honest limits
Cases and stub are author-written, so this validates the engine and
escalation logic, not LLM extraction accuracy. Not tax advice; output is
a reviewer worksheet and a qualified professional must sign off.

## 🤖 LLM Chat Logs & Audit Trail

In compliance with the assignment requirements, the full interaction history with the LLM used during research, architecture design, and prototype generation can be accessed below:

* 📁 **Download Chat Logs (ZIP):** [Click here to view/download LLM Logs](https://drive.google.com/file/d/1ngFz9uqr_0QNX9EUcP5Oq7tGVpdp-fBe/view?usp=sharing)
* **Model Used:** Claude 3.5 Sonnet / OpenAI GPT-4o
* **Summary of Chat History:**
  * Iterative refinement of the solution brief and Pydantic extraction schema
  * Design of the grounding gate and deterministic rate calculator
  * Synthesis of test cases and reporting of failure modes
