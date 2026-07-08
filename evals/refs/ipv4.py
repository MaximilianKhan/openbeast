import re

_OCTET = r"(?:25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9][0-9]|[0-9])"
IPV4_REGEX = re.compile(r"(?:" + _OCTET + r"\.){3}" + _OCTET)


def is_valid_ipv4(s):
    return IPV4_REGEX.fullmatch(s) is not None
