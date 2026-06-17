import re

msg1 = "**https://google.com**"
msg2 = "Here is a link: https://example.com/foo_bar"
msg3 = "Already wrapped <https://example.com>"

def fix(msg):
    return re.sub(r'(?<!<)(https?://[^\s<>"\'*]+)', r'<\1>', msg)

print(fix(msg1))
print(fix(msg2))
print(fix(msg3))
