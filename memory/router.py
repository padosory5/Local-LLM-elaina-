import re


class MemoryRouter:

    def __init__(self):

        self.memory_patterns = [

            r"\bmy\b",
            r"\bme\b",
            r"\bmine\b",

            r"\bremember\b",
            r"\bforget\b",

            r"\bwho am i\b",
            r"\bwhat('?s| is) my\b",

            r"\bfavorite\b",
            r"\blike\b",
            r"\blove\b",
            r"\bhate\b",
            r"\bprefer\b",

            r"\bproject\b",
            r"\bworking on\b",
            r"\bgoal\b",

            r"\bmajor\b",
            r"\bstudy\b",
            r"\bschool\b",
            r"\buniversity\b",

            r"\bname\b",
            r"\bage\b",
            r"\bbirthday\b",

            r"\bgirlfriend\b",
            r"\bfamily\b",
            r"\bfriend\b"
        ]


    def should_use_memory(self, message):

        message = message.lower()

        for pattern in self.memory_patterns:

            if re.search(pattern, message):
                return True

        return False