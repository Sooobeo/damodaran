"""Project-local, CPU-only Argos Translate runtime. No network during inference."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import sys

APP_ROOT = Path(os.environ.get("APP_ROOT", Path(__file__).resolve().parents[2])).resolve()
RUNTIME_ROOT = APP_ROOT / ".translation"
MODEL = "argos-en_ko-1.1"
BRIDGE_VERSION = "argos-bridge-v3"
AMBIGUOUS_SINGLE_WORDS = {
    "return", "capital", "primer", "value", "income", "equity", "risk", "debt",
    "growth", "spread", "interest", "statement", "compounding", "perpetuity",
    "perpetuities", "sales", "regression",
}
COPULA_PAIRS = [("이었습니다", "였습니다"), ("이었다", "였다"), ("이었고", "였고"), ("이었던", "였던"), ("이었지만", "였지만"), ("이라는", "라는"), ("이라고", "라고"), ("이라면", "라면"), ("이다", "다"), ("이며", "며"), ("이고", "고"), ("이지만", "지만"), ("이에요", "예요")]
COPULA_FIXED = {"입니다", "입니까", "인가", "인가요", "인지"}
PARTICLES = sorted({"으로부터", "로부터", "으로는", "로는", "으로도", "로도", "에서는", "에서만", "에게는", "들에게", "들은", "들이", "들을", "들과", "들로", "들", "에서", "에게", "에는", "으로", "로", "은", "는", "이", "가", "을", "를", "과", "와", "에", "의", "도", "만"} | COPULA_FIXED | {form for pair in COPULA_PAIRS for form in pair}, key=len, reverse=True)


def normalize_term(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).casefold()


def source_variants(rule: dict) -> list[str]:
    variants = [rule["source"], *rule.get("aliases", [])]
    # Preserve compatibility with the older canonical-name-plus-acronym field.
    base = re.sub(r"\s*\([A-Z][A-Z0-9 -]*\)$", "", rule["source"])
    if base != rule["source"]:
        variants.append(base)
    return sorted(set(v.strip() for v in variants if v.strip()), key=len, reverse=True)


def is_acronym(value: str) -> bool:
    return re.fullmatch(r"[A-Z][A-Z0-9/.-]{1,10}", value) is not None


def phrase_occurrences(text: str, rule: dict) -> list[tuple[int, int]]:
    matches = set()
    for variant in source_variants(rule):
        if normalize_term(variant) in AMBIGUOUS_SINGLE_WORDS:
            continue
        pattern = r"(?<![A-Za-z0-9_])" + re.escape(variant).replace(r"\ ", r"\s+") + r"(?![A-Za-z0-9_])"
        # Acronyms are case sensitive; ordinary English phrases are not.
        flags = 0 if is_acronym(variant) else re.IGNORECASE
        matches.update((m.start(), m.end()) for m in re.finditer(pattern, text, flags))
    return sorted(matches)


def target_occurrences(text: str, variants: list[str]):
    variants = sorted(set(v.strip() for v in variants if v.strip()), key=len, reverse=True)
    if not variants:
        return []
    # Korean spacing can vary. Match complete known phrases, optionally followed
    # by a known particle, never an arbitrary substring inside a larger word.
    def spaced_pattern(value: str) -> str:
        pattern = ""
        for index, character in enumerate(value):
            if character.isspace():
                pattern += r"\s*"
            else:
                if index and "가" <= value[index - 1] <= "힣" and "가" <= character <= "힣":
                    pattern += r"\s*"
                pattern += re.escape(character)
        return pattern
    choices = "|".join(spaced_pattern(v) for v in variants)
    pattern = (r"(?<![가-힣A-Za-z0-9_])(?P<term>" + choices + r")"
               + r"(?P<particle>" + "|".join(PARTICLES) + r")?(?![가-힣A-Za-z0-9_])")
    return list(re.finditer(pattern, text))


def adjusted_particle(target: str, particle: str) -> str:
    if not particle:
        return particle
    last = target.rstrip()[-1]
    if not "가" <= last <= "힣":
        return particle
    final = (ord(last) - ord("가")) % 28
    if particle in COPULA_FIXED:
        return particle
    for consonant, vowel in COPULA_PAIRS:
        if particle in (consonant, vowel):
            return consonant if final else vowel
    for consonant, vowel in [("으로", "로"), ("은", "는"), ("이", "가"), ("을", "를"), ("과", "와")]:
        for current in (consonant, vowel):
            if particle.startswith(current):
                chosen = consonant if final and not (consonant == "으로" and final == 8) else vowel
                return chosen + particle[len(current):]
    return particle


def correct_sentence_terms(source: str, translated: str, rules: list[dict]) -> dict:
    candidates = []
    for index, rule in enumerate(rules):
        if rule.get("mode", "exact") == "phrase":
            candidates.extend((start, end, index) for start, end in phrase_occurrences(source, rule))
    accepted = []
    warnings = []
    conflicting_rules = set()
    # Longest source compounds win over contained terms (e.g. net present value).
    for start, end, index in sorted(candidates, key=lambda x: (-(x[1] - x[0]), x[0], x[2])):
        overlaps = [item for item in accepted if start < item[1] and item[0] < end]
        if overlaps:
            if any(start == item[0] and end == item[1] and rules[index]["target"] != rules[item[2]]["target"] for item in overlaps):
                conflicting_rules.add(index)
                conflicting_rules.update(item[2] for item in overlaps)
            continue
        accepted.append((start, end, index))
    proposals = []
    active_rules = sorted({item[2] for item in accepted} | conflicting_rules)
    canonical_by_rule = {index: target_occurrences(translated, [rules[index]["target"]]) for index in active_rules}
    for index in active_rules:
        rule = rules[index]
        label = rule["source"]
        if index in conflicting_rules:
            warnings.append(f"용어 검토 필요: {label}의 한국어 규칙이 서로 다릅니다.")
            continue
        occurrences = sum(item[2] == index for item in accepted)
        canonical = canonical_by_rule[index]
        wrong = target_occurrences(translated, [v for v in rule.get("replacements", []) if normalize_term(v) != normalize_term(rule["target"])])
        if len(canonical) == occurrences and not wrong:
            continue
        if occurrences != 1 or len(wrong) != 1 or canonical:
            reason = "대응 위치가 모호합니다" if wrong or canonical or occurrences > 1 else "지정 번역어를 확인하지 못했습니다"
            warnings.append(f"용어 검토 필요: {label} → {rule['target']} ({reason}).")
            continue
        match = wrong[0]
        if any(match.start() < other.end() and other.start() < match.end()
               for other_index, matches in canonical_by_rule.items() if other_index != index
               for other in matches):
            warnings.append(f"용어 검토 필요: {label}의 교정 위치가 다른 원문 용어의 지정 번역어와 겹칩니다.")
            continue
        replacement = rule["target"] + adjusted_particle(rule["target"], match.group("particle") or "")
        proposals.append((match.start(), match.end(), replacement, index))
    blocked = set()
    for i, proposal in enumerate(proposals):
        for j, other in enumerate(proposals[:i]):
            if proposal[0] < other[1] and other[0] < proposal[1]:
                blocked.update([i, j])
    for i in blocked:
        warnings.append(f"용어 검토 필요: {rules[proposals[i][3]]['source']}의 교정 위치가 겹칩니다.")
    for i, (start, end, value, _index) in sorted(enumerate(proposals), key=lambda pair: pair[1][0], reverse=True):
        if i not in blocked:
            translated = translated[:start] + value + translated[end:]
    return {"translatedText": translated, "warnings": list(dict.fromkeys(warnings))}


def environment() -> None:
    # Override global Argos configuration and keep its writes inside this project.
    for key, value in {
        "XDG_DATA_HOME": RUNTIME_ROOT / "data",
        "XDG_CONFIG_HOME": RUNTIME_ROOT / "config",
        "XDG_CACHE_HOME": RUNTIME_ROOT / "cache",
        "ARGOS_PACKAGES_DIR": RUNTIME_ROOT / "packages",
        "ARGOS_DEVICE_TYPE": "cpu",
        "ARGOS_MODEL_PROVIDER": "OPENNMT",
        "ARGOS_CHUNK_TYPE": "STANZA",
        "ARGOS_COMPUTE_TYPE": "int8",
        "ARGOS_INTER_THREADS": "1",
        "ARGOS_INTRA_THREADS": "4",
        "ARGOS_BEAM_SIZE": "4",
        "ARGOS_DEBUG": "0",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
    }.items():
        os.environ[key] = str(value)


def sha256(file: Path) -> str:
    digest = hashlib.sha256()
    with file.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def local_path(relative: str) -> Path:
    if not isinstance(relative, str) or Path(relative).is_absolute():
        raise ValueError("Invalid local path")
    value = (APP_ROOT / relative).resolve()
    if not value.is_relative_to(APP_ROOT):
        raise ValueError("Invalid local path")
    return value


def runtime_version() -> str:
    return ";".join([
        BRIDGE_VERSION,
        "argos=" + importlib.metadata.version("argostranslate"),
        "ctranslate2=" + importlib.metadata.version("ctranslate2"),
        "sentencepiece=" + importlib.metadata.version("sentencepiece"),
        "spacy=" + importlib.metadata.version("spacy"),
        "cpu-int8-beam4-spacy-rule-sentences",
    ])


def verify_manifest() -> dict:
    manifest = json.loads((RUNTIME_ROOT / "manifest.json").read_text("utf-8"))
    if (manifest.get("schemaVersion") != 1 or manifest.get("provider") != "argos"
            or manifest.get("model") != MODEL or manifest.get("runtimeVersion") != runtime_version()):
        raise ValueError("Runtime identity changed")
    if not local_path(manifest["pythonPath"]).is_file() or not local_path(manifest["modelPath"]).is_dir():
        raise ValueError("Local runtime missing")
    files = manifest.get("modelFiles", [])
    if not files or not re.fullmatch(r"[a-f0-9]{64}", manifest.get("modelHash", "")):
        raise ValueError("Invalid model manifest")
    model_path = local_path(manifest["modelPath"])
    seen = set()
    for item in files:
        file = local_path(item["path"])
        if not file.is_relative_to(model_path) or file in seen:
            raise ValueError("Invalid model file reference")
        seen.add(file)
        if not file.is_file() or file.stat().st_size != item["size"] or sha256(file) != item["sha256"]:
            raise ValueError("Model integrity check failed")
    actual = {file.resolve() for file in model_path.rglob("*") if file.is_file()}
    if actual != seen:
        raise ValueError("Model inventory changed")
    canonical = json.dumps(files, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if hashlib.sha256(canonical).hexdigest() != manifest["modelHash"]:
        raise ValueError("Model identity changed")
    return manifest


def disable_network() -> None:
    # Audit hooks cannot be removed by imported libraries. Neither a model miss nor
    # third-party defaults may silently download or send input out of this process.
    def audit(event, _args):
        if event in {"socket.connect", "socket.connect_ex", "socket.getaddrinfo", "socket.sendto"}:
            raise RuntimeError("Network is disabled for local translation")
    sys.addaudithook(audit)


class LocalTranslator:
    def __init__(self, manifest: dict):
        environment()
        disable_network()
        import spacy
        import ctranslate2
        from argostranslate import package, translate

        pkg = package.Package(local_path(manifest["modelPath"]))
        if pkg.from_code != "en" or pkg.to_code != "ko" or pkg.package_version != "1.1":
            raise ValueError("Unexpected language package")
        self.nlp = spacy.blank("en")
        self.nlp.add_pipe("sentencizer")
        self.tokenizer = pkg.tokenizer
        self.translator = translate.PackageTranslation(translate.Language("en", "English"), translate.Language("ko", "Korean"), pkg)
        native = ctranslate2.Translator(str(pkg.package_path / "model"), device="cpu", compute_type="int8", inter_threads=1, intra_threads=4)

        class BoundedTranslator:
            def translate_batch(self, *args, **kwargs):
                # Argos's default decoder limit can truncate long Korean output.
                # Split source first, disable input truncation, and reject output
                # that reaches the explicit decoder limit instead of saving it.
                kwargs["max_input_length"] = 0
                kwargs["max_decoding_length"] = 1024
                results = native.translate_batch(*args, **kwargs)
                if any(len(hypothesis) >= 1024 for result in results for hypothesis in result.hypotheses):
                    raise RuntimeError("Local translation reached decoder limit")
                return results

        self.translator.translator = BoundedTranslator()
        # Use spaCy's local rule-based English sentencizer. Its blank pipeline needs
        # no model download; packaged old Stanza assets are not initialized.
        self.translator.sentencizer = self

    def split_sentences(self, text: str) -> list[str]:
        sentences = []
        for sentence in self.nlp(text).sents:
            words = re.findall(r"\S+\s*", sentence.text)
            chunk = ""
            for word in words:
                if chunk and len(self.tokenizer.encode(chunk + word)) > 400:
                    sentences.append(chunk.strip())
                    chunk = ""
                if len(self.tokenizer.encode(word)) > 400:
                    raise ValueError("Input token is too long")
                chunk += word
            if chunk.strip():
                sentences.append(chunk.strip())
        return sentences

    def translate_plain(self, text: str) -> str:
        if not re.search(r"[A-Za-z]", text):
            return text
        leading = text[:len(text) - len(text.lstrip())]
        trailing = text[len(text.rstrip()):]
        result = self.translator.translate(text.strip())
        if not result.strip():
            raise ValueError("Empty translation")
        return leading + result.strip() + trailing

    def source_sentences(self, text: str) -> list[str]:
        # Include original inter-sentence whitespace in each piece.
        pieces = []
        cursor = 0
        for sentence in self.nlp(text).sents:
            pieces.append(text[cursor:sentence.end_char])
            cursor = sentence.end_char
        if cursor < len(text):
            if pieces:
                pieces[-1] += text[cursor:]
            else:
                pieces.append(text)
        return pieces or [text]

    def translate_segment_result(self, text: str, glossary: list[dict]) -> dict:
        exact = []
        warnings = []
        normalized = normalize_term(text)
        for rule in glossary:
            variants = [variant for variant in source_variants(rule) if normalized == normalize_term(variant)]
            if not variants:
                continue
            # A standalone label supplies no context for choosing a financial
            # sense. The rule's editorial note is not evidence of that context.
            if normalized in AMBIGUOUS_SINGLE_WORDS:
                warnings.append(f"용어 검토 필요: {text.strip()}는 문맥 없이 금융 의미로 확정할 수 없는 단독 표현입니다.")
                continue
            if not any(not is_acronym(variant) or text.strip() == variant for variant in variants):
                warnings.append(f"용어 검토 필요: {text.strip()}의 대소문자가 금융 약어와 다릅니다. 원문 문맥을 확인하세요.")
                continue
            exact.append(rule)
        targets = {rule["target"] for rule in exact}
        if len(targets) == 1 and not warnings:
            leading = text[:len(text) - len(text.lstrip())]
            trailing = text[len(text.rstrip()):]
            return {"translatedText": leading + next(iter(targets)) + trailing, "warnings": []}
        if len(targets) > 1:
            warnings.append("용어 검토 필요: 전체 일치 규칙의 한국어 번역이 서로 다릅니다.")
        # Symbols and protected values bypass MT, so the engine cannot alter,
        # reorder, duplicate, or drop them. Never send synthetic tokens into MT.
        parts = re.split(r"(__PV_[a-f0-9]+_\d+__|[=<>≤≥±×÷])", text)
        output = []
        for part in parts:
            if re.fullmatch(r"__PV_[a-f0-9]+_\d+__|[=<>≤≥±×÷]", part or ""):
                output.append(part)
            elif not re.search(r"[A-Za-z]", part):
                output.append(part)
            else:
                for sentence in self.source_sentences(part):
                    result = correct_sentence_terms(sentence, self.translate_plain(sentence), glossary)
                    output.append(result["translatedText"])
                    warnings.extend(result["warnings"])
        return {"translatedText": "".join(output), "warnings": list(dict.fromkeys(warnings))}

    def translate_segment(self, text: str, glossary: list[dict]) -> str:
        return self.translate_segment_result(text, glossary)["translatedText"]
