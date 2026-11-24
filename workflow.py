"""Order tagged playlists and persist each workflow's result independently."""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date

from virtualdj import parse_m3u

LOGGER = logging.getLogger(__name__)
TAG_GROUP = re.compile(r" (?P<group>\([^()]*\)|\[[^\[\]]*\])\.m3u$")
SET_NAME = re.compile(r"(?P<date>[0-9]{4}\.[0-9]{2}\.[0-9]{2}) (?:(?P<part>[0-9]{2}) )?(?P<event>.+)")
WORKFLOWS = ("DH", "S")
VALID_TOKENS = {code + marker for code in WORKFLOWS for marker in ("", " ✅", " ❌")}


@dataclass(frozen=True)
class SetFile:
    file_id: str
    stem: str
    delimiter: str
    tokens: tuple[str, ...]
    played_on: date
    part: int | None
    event: str

    @property
    def spotify_name(self):
        return f"{self.played_on:%Y.%m.%d} - {self.event}"

    def filename(self, tokens):
        closing = ")" if self.delimiter == "(" else "]"
        return f"{self.stem} {self.delimiter}{'; '.join(tokens)}{closing}.m3u"


def parse_filename(file_id, filename):
    group = TAG_GROUP.search(filename)
    if group is None:
        return None
    tokens = tuple(token.strip() for token in group["group"][1:-1].split(";"))
    if not any(token in WORKFLOWS for token in tokens):
        return None
    if any(token not in VALID_TOKENS for token in tokens):
        raise ValueError("Invalid workflow token")
    if len({token.split()[0] for token in tokens}) != len(tokens):
        raise ValueError("Duplicate workflow token")

    stem = filename[:group.start()]
    fields = SET_NAME.fullmatch(stem)
    if fields is None or not fields["event"].strip():
        raise ValueError("Expected a date and event name")
    played_on = date.fromisoformat(fields["date"].replace(".", "-"))
    part = int(fields["part"]) if fields["part"] is not None else None
    return SetFile(file_id, stem, group["group"][0], tokens, played_on, part, fields["event"].strip())


def run_workflows(files, download, rename, outputs):
    """Run configured output callbacks and return a process exit code.

    Files are (Drive ID, filename) pairs. Each output receives a SetFile
    and ordered parsed tracks. Callbacks raise on failure.
    """
    playlists = []
    failed = False
    for file_id, filename in files:
        try:
            playlist = parse_filename(file_id, filename)
        except ValueError:
            LOGGER.error("Filename validation failed")
            failed = True
            continue
        if playlist is not None and any(token in outputs for token in playlist.tokens):
            playlists.append(playlist)

    history_parts = {}
    if "DH" in outputs:
        for playlist in playlists:
            if "DH" in playlist.tokens:
                key = (playlist.played_on, playlist.event)
                history_parts.setdefault(key, Counter())[playlist.part] += 1
    ambiguous = {
        key for key, parts in history_parts.items()
        if any(count > 1 for count in parts.values()) or (None in parts and len(parts) > 1)
    }

    playlists.sort(key=lambda item: (
        item.played_on, item.event, -1 if item.part is None else item.part, item.file_id
    ))
    for playlist in playlists:
        tracks = None
        stage = "download"
        try:
            contents = download(playlist.file_id)
            stage = "parse"
            tracks = parse_m3u(contents)
            if not tracks:
                raise ValueError("Playlist contains no tracks")
        except Exception:
            tracks = None
            LOGGER.error("Playlist %s failed", stage)

        tokens = list(playlist.tokens)
        for index, token in enumerate(playlist.tokens):
            if token not in WORKFLOWS or token not in outputs:
                continue
            success = False
            if token == "DH" and (playlist.played_on, playlist.event) in ambiguous:
                LOGGER.error("DH ordering validation failed")
            elif tracks is not None:
                try:
                    outputs[token](playlist, tracks)
                except Exception:
                    LOGGER.error("%s output failed", token)
                else:
                    success = True
            failed |= not success
            tokens[index] = f"{token} {'✅' if success else '❌'}"
            try:
                rename(playlist.file_id, playlist.filename(tokens))
            except Exception:
                LOGGER.error(
                    "%s status rename failed (output confirmed: %s). "
                    "The workflow may still be pending.", token, success,
                )
                failed = True
                break

    return int(failed)
