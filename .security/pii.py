"""Offline Presidio pattern recognizers; no NLP models or network requests."""

import datetime
import re

import tldextract
from presidio_analyzer import Pattern, PatternRecognizer
from presidio_analyzer.predefined_recognizers import CreditCardRecognizer, EmailRecognizer


class OfflineEmailRecognizer(EmailRecognizer):
    # The default EmailRecognizer may refresh the public suffix list over HTTP.
    # Use the bundled suffix snapshot only and avoid writing a global cache.
    extractor = tldextract.TLDExtract(cache_dir=None, suffix_list_urls=())

    def validate_result(self, value):
        return self.extractor(value).fqdn != ""


class ChineseIdentityRecognizer(PatternRecognizer):
    def __init__(self):
        super().__init__(
            supported_entity="CN_ID_CARD",
            patterns=[Pattern("18-digit ID", r"(?<![0-9A-Za-z])[1-9][0-9]{16}[0-9Xx](?![0-9A-Za-z])", 0.8)],
        )

    def validate_result(self, value):
        try:
            birthday = datetime.datetime.strptime(value[6:14], "%Y%m%d").date()
        except ValueError:
            return False
        if birthday > datetime.date.today() or birthday.year < 1800:
            return False
        weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
        checksum = sum(int(digit) * weight for digit, weight in zip(value[:17], weights))
        return value[-1].upper() == "10X98765432"[checksum % 11]


def make_recognizers(policy):
    recognizers = [
        OfflineEmailRecognizer(),
        CreditCardRecognizer(),
        ChineseIdentityRecognizer(),
        PatternRecognizer(
            supported_entity="CN_MOBILE_PHONE",
            patterns=[Pattern("CN mobile", r"(?<![0-9A-Za-z])(?:\+?86[ -]?)?1[3-9][0-9](?:[ -]?[0-9]{4}){2}(?![0-9A-Za-z])", 0.8)],
        ),
    ]
    for item in policy["custom_patterns"]:
        recognizers.append(PatternRecognizer(
            supported_entity=item["entity"],
            patterns=[Pattern(item["entity"], item["regex"], 0.9)],
        ))
    return recognizers


def detect(text, recognizers, policy):
    """Return locations and entity types only; never return the sensitive values."""
    found = set()
    for recognizer in recognizers:
        for result in recognizer.analyze(text, recognizer.supported_entities):
            if result.score < 0.5:
                continue
            value = text[result.start:result.end]
            if any(
                item["entity"] == result.entity_type
                and re.fullmatch(item["regex"], value)
                for item in policy["pii_allowlist"]
            ):
                continue
            line = text.count("\n", 0, result.start) + 1
            found.add((line, result.entity_type))
    return sorted(found)
