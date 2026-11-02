"""Validation and time transforms for chapter and section resources."""
import math
from urllib.parse import urlsplit


def validate_resources(value):
    if not isinstance(value, list) or len(value) > 50:
        raise ValueError("Choose at most 50 further-information links per chapter or section.")
    result = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("Each further-information link must be an object.")
        title, url = item.get("title"), item.get("url")
        if not isinstance(title, str) or not title.strip() or len(title) > 500:
            raise ValueError("Link titles must contain 1 to 500 characters.")
        if not isinstance(url, str) or len(url) > 2048 or any(ord(char) <= 32 for char in url):
            raise ValueError("Links must be HTTP or HTTPS URLs without spaces.")
        try:
            parsed = urlsplit(url)
            valid = parsed.scheme.lower() in {"http", "https"} and parsed.hostname and not parsed.username and not parsed.password
            parsed.port
        except ValueError as error:
            raise ValueError("Enter a valid HTTP or HTTPS URL.") from error
        if not valid:
            raise ValueError("Enter a valid HTTP or HTTPS URL without embedded credentials.")
        link = {"title": title.strip(), "url": url}
        start, end = item.get("start"), item.get("end")
        if start is not None or end is not None:
            if (type(start) not in (int, float) or type(end) not in (int, float)
                    or not math.isfinite(start) or not math.isfinite(end) or not 0 <= start < end):
                raise ValueError("Custom link times must be finite, with End after Start and Start at least zero.")
            link.update(start=start, end=end)
        result.append(link)
    return result


def retime_resources(resources, start, end, rate=1, offset=0):
    result = []
    for resource in resources:
        link = dict(resource)
        if "start" in link:
            left, right = max(start, link["start"]), min(end, link["end"])
            if left >= right:
                continue
            link.update(start=round((left - start) / rate + offset, 6),
                        end=round((right - start) / rate + offset, 6))
        result.append(link)
    return result
