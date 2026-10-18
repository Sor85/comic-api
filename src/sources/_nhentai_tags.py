"""Conservative JM theme-tag aliases verified against nhentai's tag directory.

Only exact aliases are translated; native nhentai queries bypass this table.
Sources and intentionally omitted terms are documented in docs/nhentai-tags.md.
"""

_JM_TAG_ALIASES = (
    ("josei", ("女性向",)),
    ("full color", ("全彩",)),
    ("incest", ("近親", "近亲")),
    ("yuri", ("百合",)),
    ("yaoi", ("YAOI",)),
    ("gender bender", ("性轉", "性转")),
    ("netorare", ("NTR",)),
    ("big breasts", ("巨乳",)),
    ("small breasts", ("貧乳", "贫乳")),
    ("teacher", ("教師", "教师")),
    ("maid", ("女僕", "女仆")),
    ("nurse", ("護士", "护士")),
    ("glasses", ("眼鏡", "眼镜")),
    ("pantyhose", ("連褲襪", "连裤袜")),
    ("bunny girl", ("兔女郎",)),
    ("group", ("群交",)),
    ("footjob", ("足交",)),
    ("bdsm", ("SM",)),
    ("anal", ("肛交",)),
    ("ahegao", ("阿黑顏", "阿黑颜")),
    ("drugs", ("藥物", "药物")),
    ("futanari", ("扶他",)),
    ("masturbation", ("自慰",)),
    ("tentacles", ("觸手", "触手")),
)

JM_TAG_QUERIES = {alias.casefold(): f'tag:"{name}"'
                  for name, aliases in _JM_TAG_ALIASES for alias in aliases}
