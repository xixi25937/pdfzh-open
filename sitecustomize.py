"""Runtime compatibility settings for the local BabelDOC subprocess.

BabelDOC's embedded metadata expands every language family with fonts for all
other supported languages. For an English-to-Simplified-Chinese tool this adds
hundreds of megabytes and makes startup depend on dozens of unrelated files.
Keep the Chinese family focused while retaining universal fallback fonts.
"""

from __future__ import annotations

import logging


def _patch_babeldoc_fonts() -> None:
    try:
        from babeldoc.assets import embedding_assets_metadata as metadata
    except ImportError:
        return

    minimal_cn = {
        "script": ["LXGWWenKaiGB-Regular.1.520.ttf"],
        "normal": [
            "SourceHanSerifCN-Bold.ttf",
            "SourceHanSerifCN-Regular.ttf",
            "SourceHanSansCN-Bold.ttf",
            "SourceHanSansCN-Regular.ttf",
        ],
        "fallback": [
            "GoNotoKurrent-Regular.ttf",
            "GoNotoKurrent-Bold.ttf",
        ],
        "base": ["SourceHanSansCN-Regular.ttf"],
    }
    for key, value in minimal_cn.items():
        metadata.CN_FONT_FAMILY[key] = list(value)

    original = metadata.get_font_family

    def get_font_family(lang_code: str):
        normalized = lang_code.upper().replace("-", "_")
        if normalized == "CN" or normalized.startswith("ZH"):
            return metadata.CN_FONT_FAMILY
        return original(lang_code)

    metadata.get_font_family = get_font_family


_patch_babeldoc_fonts()


def _patch_empty_translation_retry() -> None:
    """Never accept or cache an empty translation for non-empty input."""
    try:
        from babeldoc.translator.translator import BaseTranslator
    except ImportError:
        return

    logger = logging.getLogger("pdfzh.translation_retry")

    def wrap(method_name: str) -> None:
        original = getattr(BaseTranslator, method_name)
        if getattr(original, "_pdfzh_nonempty_retry", False):
            return

        def checked(self, text, ignore_cache=False, rate_limit_params=None):
            if not str(text or "").strip():
                return original(
                    self,
                    text,
                    ignore_cache=ignore_cache,
                    rate_limit_params=rate_limit_params,
                )
            for attempt in range(3):
                result = original(
                    self,
                    text,
                    ignore_cache=ignore_cache or attempt > 0,
                    rate_limit_params=rate_limit_params,
                )
                if isinstance(result, str) and result.strip():
                    if attempt > 0 and not (self.ignore_cache or ignore_cache):
                        try:
                            self.cache.set(text, result)
                        except Exception:
                            logger.debug("Unable to replace empty translation cache")
                    return result
                logger.warning(
                    "Empty translation response; retrying (%s/3)", attempt + 1
                )
            raise RuntimeError("DeepSeek returned an empty translation three times")

        checked._pdfzh_nonempty_retry = True
        setattr(BaseTranslator, method_name, checked)

    wrap("translate")
    wrap("llm_translate")


_patch_empty_translation_retry()
