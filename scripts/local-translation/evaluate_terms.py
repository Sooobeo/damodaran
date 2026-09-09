"""Manual, offline terminology evaluation against the unchanged Argos model.

Writes only public/synthetic samples under .translation. With --wait, keeps the
model alive while the curated rule file is reviewed, then evaluates those rules
against the identical cached sentence translations after one stdin newline.
"""
from __future__ import annotations

import json
import hashlib
import sys
import time
from datetime import datetime, timezone

from runtime import APP_ROOT, LocalTranslator, correct_sentence_terms, verify_manifest


SENTENCES = {
    "FT01": "Financial statements provide information about a company.",
    "FT02": "Accounting statements describe the past performance of a company.",
    "FT03": "The balance sheet reports the assets and liabilities of a company.",
    "FT04": "The income statement reports revenues and expenses.",
    "FT05": "The statement of cash flows reports changes in cash.",
    "FT06": "We calculate the present value of the future cash flows.",
    "FT07": "The future value depends on the interest rate and the time period.",
    "FT08": "The net present value of this project is positive.",
    "FT09": "The discount rate reflects the risk of the cash flows.",
    "FT10": "The internal rate of return exceeds the required return.",
    "FT11": "The riskfree rate is an input to the valuation.",
    "FT12": "The equity risk premium measures the additional return required by investors.",
    "FT13": "The cost of equity is the return required by shareholders.",
    "FT14": "The cost of debt depends on the credit risk of the company.",
    "FT15": "The cost of capital is used to discount cash flows.",
    "FT16": "The weighted average cost of capital reflects the financing mix.",
    "FT17": "The default spread increases when the credit risk rises.",
    "FT18": "The interest coverage ratio measures the ability to pay interest.",
    "FT19": "The return on equity measures profitability relative to shareholder funds.",
    "FT20": "The return on invested capital measures the profitability of operations.",
    "FT21": "The operating income increased during the year.",
    "FT22": "The net income includes the effect of interest and taxes.",
    "FT23": "The capital expenditures include investments in new equipment.",
    "FT24": "Depreciation and amortization are noncash expenses.",
    "FT25": "The working capital changes as the business grows.",
    "FT26": "The non-cash working capital excludes cash from the calculation.",
    "FT27": "The reinvestment rate affects the expected growth of the company.",
    "FT28": "The free cash flow to the firm is available to all providers of capital.",
    "FT29": "The free cash flow to equity is available to shareholders.",
    "FT31": "The stable growth assumption affects the valuation.",
    "FT32": "The intrinsic value can differ from the market price.",
    "FT33": "The enterprise value includes the value attributable to lenders.",
    "FT34": "The equity value is attributable to shareholders.",
    "FT35": "The market capitalization depends on the share price.",
    "FT36": "The book value is reported in the accounts.",
    "FT37": "The price earnings ratio compares the stock price with earnings.",
    "FT38": "The price to book ratio compares market price with accounting value.",
    "FT39": "The unlevered beta removes the effect of financial leverage.",
    "FT40": "The bottom-up beta uses information from comparable companies.",
    "FT41": "The current assets include cash and inventory.",
    "FT42": "The current liabilities include obligations due within a year.",
    "FT43": "The earnings per share increased during the year.",
}


def main():
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    started = time.monotonic()
    manifest = verify_manifest()
    cached = "--cached" in sys.argv
    translator = None if cached else LocalTranslator(manifest)
    rules_path = APP_ROOT / "content/translation-glossary.json"
    rules = json.loads(rules_path.read_text("utf-8"))["rules"]
    samples = [{"id": rule["id"], "source": SENTENCES.get(rule["id"], rule["source"]),
                "provenance": "synthetic evaluation sentence" if rule["id"] in SENTENCES else "curated exact term/title"}
               for rule in rules]
    public_probe = APP_ROOT / ".translation/probe-input.json"
    if public_probe.exists():
        samples.extend({"id": "R01-" + item["id"], "source": item["text"],
                        "provenance": "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/AccPrimer/accstate.htm"}
                       for item in json.loads(public_probe.read_text("utf-8")))
    baseline_path = APP_ROOT / ".translation/term-evaluation-baseline.json"
    if cached:
        previous = json.loads(baseline_path.read_text("utf-8"))
        if previous["modelHash"] != manifest["modelHash"] or previous["runtimeVersion"] != manifest["runtimeVersion"]:
            raise ValueError("Cached baseline model/runtime differs")
        samples = previous["samples"]
    else:
        for sample in samples:
            sample["sentences"] = [{"source": sentence, "plainMT": translator.translate_plain(sentence)}
                                   for sentence in translator.source_sentences(sample["source"])]
            sample["baseline"] = "".join(item["plainMT"] for item in sample["sentences"])
    artifact = {"baselineMode": "unchanged Argos plain MT; whole source sentences; no glossary",
                "model": manifest["model"], "modelHash": manifest["modelHash"],
                "runtimeVersion": manifest["runtimeVersion"], "generatedAt": datetime.now(timezone.utc).isoformat(),
                "samples": samples}
    if not cached:
        baseline_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps({"baselineReady": True, "samples": len(samples), "seconds": round(time.monotonic()-started, 2), "path": str(baseline_path)}), flush=True)
    if "--wait" in sys.argv:
        sys.stdin.readline()
    rules = json.loads(rules_path.read_text("utf-8"))["rules"]
    artifact["ruleCount"] = len(rules)
    artifact["glossaryVersion"] = json.loads(rules_path.read_text("utf-8"))["version"]
    artifact["glossarySha256"] = hashlib.sha256(rules_path.read_bytes()).hexdigest()
    artifact["baselineGeneratedAt"] = previous["generatedAt"] if cached else artifact["generatedAt"]
    for sample in samples:
        if any(rule["mode"] == "exact" and sample["source"] == rule["source"] for rule in rules):
            # A whole-cell rule needs no model. Use the public exact-match path.
            exact_translator = translator or LocalTranslator.__new__(LocalTranslator)
            result = exact_translator.translate_segment_result(sample["source"], rules)
        else:
            results = [correct_sentence_terms(item["source"], item["plainMT"], rules) for item in sample["sentences"]]
            result = {"translatedText": "".join(item["translatedText"] for item in results),
                      "warnings": list(dict.fromkeys(warning for item in results for warning in item["warnings"]))}
        sample["after"] = result["translatedText"]
        sample["warnings"] = result["warnings"]
        sample["changed"] = sample["baseline"] != sample["after"]
    output_path = APP_ROOT / ".translation/term-evaluation.json"
    output_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps({"complete": True, "changedSamples": sum(item["changed"] for item in samples),
                      "reviewSamples": sum(bool(item["warnings"]) for item in samples), "path": str(output_path)}), flush=True)


if __name__ == "__main__":
    main()
