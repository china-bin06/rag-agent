import json
import re


# Match whole JSON string characters, including escaped surrogate pairs.
STRING_CHAR = re.compile(
    r'[^"\\\x00-\x1f]|\\(?:["\\/bfnrt]|'
    r'u[dD][89aAbB][0-9a-fA-F]{2}\\u[dD][c-fC-F][0-9a-fA-F]{2}|'
    r'u(?![dD][89a-fA-F])[0-9a-fA-F]{4})'
)


class AnswerDraft:
    """Extract only the top-level answer string from a growing JSON object."""

    def __init__(self):
        self.text = ""
        self.position = 0
        self.state = "start"
        self.decoder = json.JSONDecoder()

    def feed(self, fragment: str) -> str:
        if self.state == "done":
            return ""
        self.text += fragment
        while self.state != "answer":
            while self.position < len(self.text) and self.text[self.position] in " \r\n\t":
                self.position += 1
            if self.position == len(self.text):
                return ""
            char = self.text[self.position]
            if self.state == "start" and char == "{":
                self.state = "key"
            elif self.state == "separator" and char == ",":
                self.state = "key"
            elif self.state in {"key", "value"}:
                try:
                    value, end = self.decoder.raw_decode(self.text, self.position)
                except json.JSONDecodeError:
                    return ""
                if self.state == "key" and isinstance(value, str):
                    self.state = "answer_colon" if value == "answer" else "colon"
                elif self.state == "value":
                    self.state = "separator"
                else:
                    break
                self.position = end
                continue
            elif self.state == "colon" and char == ":":
                self.state = "value"
            elif self.state == "answer_colon" and char == ":":
                self.state = "answer_quote"
            elif self.state == "answer_quote" and char == '"':
                self.state = "answer"
            else:
                break
            self.position += 1

        if self.state != "answer":
            self.state = "done"
            return ""

        start = self.position
        while match := STRING_CHAR.match(self.text, self.position):
            self.position = match.end()
        if self.text[self.position:self.position + 1] == '"':
            self.state = "done"
        return json.loads('"' + self.text[start:self.position] + '"')
