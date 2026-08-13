"""
Converts a DFM rules catalog from a CSV file to the JSON format
used by the analysis pipeline.
"""

import csv
import json
import re
from pathlib import Path
from typing import Optional, Dict, Any


def _parse_predicate(text: str) -> Optional[Dict[str, Any]]:
    """
    Parses a free-text threshold string into a structured predicate object.
    Handles simple values, ranges, and operators.
    """
    if not text:
        return None

    text = text.strip()
    # Normalize common dash variants and replacement characters from CSV decoding.
    normalized_text = re.sub(r"[\u2010-\u2015\u2212\ufffd]", "-", text)

    # Some rows list several published ranges. Use the first range as the
    # primary threshold and retain the original text for traceability.
    range_match = re.search(
        r"(?P<minimum>\d+(?:\.\d+)?)\s*-\s*(?P<maximum>\d+(?:\.\d+)?)",
        normalized_text,
    )
    if range_match:
        return {
            "type": "range",
            "min": float(range_match.group("minimum")),
            "max": float(range_match.group("maximum")),
        }

    numbers = [float(n) for n in re.findall(r"[-+]?\d*\.\d+|[-+]?\d+", normalized_text)]

    # Case 1: A range is specified without a directly matched decimal range.
    if len(numbers) == 2:
        return {"type": "range", "min": min(numbers), "max": max(numbers)}

    # Case 2: A single number is specified, check for operators
    if len(numbers) == 1:
        num = numbers[0]
        operator = ">="  # Default operator

        if text.startswith("<="):
            operator = "<="
        elif text.startswith("<"):
            operator = "<"
        elif text.startswith(">="):
            operator = ">="
        elif text.startswith(">"):
            operator = ">"
        # Handle cases like "4xD max" where the intent is a maximum limit
        elif (
            "max" in text.lower() or "upper" in text.lower() or "limit" in text.lower()
        ):
            operator = "<="

        return {"type": "simple", "operator": operator, "threshold": num}

    # Case 3: No parsable numbers found
    return None


def convert_csv_to_json(csv_path: Path, json_path: Path):
    rules = []
    with open(csv_path, mode="r", encoding="windows-1252", newline="") as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            # Map the exact CSV headers to your new JSON schema
            rule = {
                "rule_id": row.get("rule_id", ""),
                "rule_name": row.get("rule_name", ""),
                "process_family": row.get("process_family", ""),
                "category": row.get("category", ""),
                "guideline_ref": row.get("guideline_ref", ""),
                "provenance": row.get("provenance", ""),
                "kind": row.get("rule_kind", ""),
                "metric": row.get("parameter", "") or None,
                "units": row.get("units", "") or None,
                "severity": row.get("severity_suggestion", ""),
                "description": row.get("check_description", ""),
                "predicate": None,  # Default to None
            }

            # For quantitative rules, create the predicate block.
            if rule["kind"] == "quantitative":
                threshold_text = row.get("typical_threshold", "")
                # Use the new, more intelligent parser
                predicate = _parse_predicate(threshold_text)
                if predicate:
                    # Add the original text for traceability
                    predicate["original_csv_text"] = threshold_text
                    rule["predicate"] = predicate

            rules.append(rule)

    with open(json_path, mode="w", encoding="utf-8") as jsonfile:
        json.dump(rules, jsonfile, indent=2)

    print(f"Successfully converted {len(rules)} rules from {csv_path} to {json_path}")


if __name__ == "__main__":
    # Ensure paths point one level up to the backend directory
    INPUT_CSV = Path(__file__).parent.parent / "dfx_rules_catalog.csv"
    OUTPUT_JSON = Path(__file__).parent.parent / "rules_catalog.json"

    convert_csv_to_json(INPUT_CSV, OUTPUT_JSON)
