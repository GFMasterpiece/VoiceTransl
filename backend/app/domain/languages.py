"""Common language suggestions, never a provider support or validation list."""

from pydantic import BaseModel, ConfigDict, field_validator


class LanguageCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    names: dict[str, str]
    native_name: str
    aliases: list[str]

    @field_validator("code", "native_name")
    @classmethod
    def safe_text(cls, value: str) -> str:
        if (
            not value
            or value != value.strip()
            or len(value) > 128
            or any(not character.isprintable() for character in value)
        ):
            raise ValueError("language text must be safe and non-empty")
        return value

    @field_validator("names")
    @classmethod
    def localized_names(cls, value: dict[str, str]) -> dict[str, str]:
        if set(value) != {"zh-CN", "en"}:
            raise ValueError("language names require zh-CN and en")
        for name in value.values():
            cls.safe_text(name)
        return value

    @field_validator("aliases")
    @classmethod
    def search_aliases(cls, value: list[str]) -> list[str]:
        for alias in value:
            cls.safe_text(alias)
        return value


def validate_language_catalog(items: list[LanguageCandidate]) -> list[LanguageCandidate]:
    codes = [item.code.casefold() for item in items]
    if len(set(codes)) != len(codes):
        raise ValueError("language codes must be unique ignoring case")
    return items


# Suggestions are deliberately independent of any ASR model's supported languages.
_LANGUAGES = validate_language_catalog(
    [
        LanguageCandidate(
            code=code, names={"zh-CN": zh, "en": en}, native_name=native, aliases=aliases
        )
        for code, zh, en, native, aliases in [
            ("zh", "中文", "Chinese", "中文", ["普通话", "汉语", "Mandarin", "zho", "chi"]),
            ("en", "英语", "English", "English", ["eng"]),
            ("ja", "日语", "Japanese", "日本語", ["日文", "jpn"]),
            ("ko", "韩语", "Korean", "한국어", ["朝鲜语", "kor"]),
            ("yue", "粤语", "Cantonese", "粵語", ["广东话"]),
            ("fr", "法语", "French", "Français", ["fra", "fre"]),
            ("de", "德语", "German", "Deutsch", ["deu", "ger"]),
            ("es", "西班牙语", "Spanish", "Español", ["西语", "spa"]),
            ("pt", "葡萄牙语", "Portuguese", "Português", ["葡语", "por"]),
            ("it", "意大利语", "Italian", "Italiano", ["ita"]),
            ("ru", "俄语", "Russian", "Русский", ["rus"]),
            ("ar", "阿拉伯语", "Arabic", "العربية", ["ara"]),
            ("hi", "印地语", "Hindi", "हिन्दी", ["hin"]),
            ("th", "泰语", "Thai", "ไทย", ["tha"]),
            ("vi", "越南语", "Vietnamese", "Tiếng Việt", ["vie"]),
            ("id", "印度尼西亚语", "Indonesian", "Bahasa Indonesia", ["印尼语", "ind"]),
            ("ms", "马来语", "Malay", "Bahasa Melayu", ["msa", "may"]),
            ("tr", "土耳其语", "Turkish", "Türkçe", ["tur"]),
            ("nl", "荷兰语", "Dutch", "Nederlands", ["nld", "dut"]),
            ("pl", "波兰语", "Polish", "Polski", ["pol"]),
            ("uk", "乌克兰语", "Ukrainian", "Українська", ["ukr"]),
            ("sv", "瑞典语", "Swedish", "Svenska", ["swe"]),
            ("da", "丹麦语", "Danish", "Dansk", ["dan"]),
            ("fi", "芬兰语", "Finnish", "Suomi", ["fin"]),
            ("el", "希腊语", "Greek", "Ελληνικά", ["ell", "gre"]),
            ("he", "希伯来语", "Hebrew", "עברית", ["heb"]),
            ("cs", "捷克语", "Czech", "Čeština", ["ces", "cze"]),
            ("ro", "罗马尼亚语", "Romanian", "Română", ["ron", "rum"]),
            ("hu", "匈牙利语", "Hungarian", "Magyar", ["hun"]),
            ("fa", "波斯语", "Persian", "فارسی", ["fas", "per"]),
        ]
    ]
)


def language_catalog() -> list[LanguageCandidate]:
    return [item.model_copy(deep=True) for item in _LANGUAGES]
