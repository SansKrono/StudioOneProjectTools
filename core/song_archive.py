from pathlib import Path
import zipfile


# Archive members that can contain audio references and are safe to edit as UTF-8 text.
TEXT_MEMBER_SUFFIXES = (".xml", ".txt", ".settings", ".songdata", ".json")


def is_song_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() == ".song" and zipfile.is_zipfile(path)


def read_text_from_archive(song_path: Path, member_name: str, encoding: str = "utf-8") -> str:
    with zipfile.ZipFile(song_path, "r") as z:
        data = z.read(member_name)
    return data.decode(encoding, errors="replace")


def read_text_members(song_path: Path) -> list[str]:
    """Return the decoded contents of every text-like member of a .song archive."""
    texts = []
    with zipfile.ZipFile(song_path, "r") as z:
        for name in z.namelist():
            if name.lower().endswith(TEXT_MEMBER_SUFFIXES):
                texts.append(z.read(name).decode("utf-8", errors="replace"))
    return texts


def write_modified_song_copy(song_path: Path, output_path: Path, transform) -> int:
    """
    Write a copy of a .song archive, passing each text member through transform.

    transform(member_name, text) returns the new text, or None to keep the member unchanged.
    Binary members and unchanged members are copied byte-for-byte.
    Returns the number of members that were changed.

    The copy is written to a temporary file first and then moved into place,
    so a failure never leaves a half-written .song behind. Writing over the
    source archive is refused.
    """
    if output_path.resolve() == song_path.resolve():
        raise ValueError("Refusing to overwrite the original .song file.")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_name(output_path.name + ".tmp")
    changed = 0

    try:
        with zipfile.ZipFile(song_path, "r") as zin, zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as zout:
            for info in zin.infolist():
                data = zin.read(info.filename)

                if info.filename.lower().endswith(TEXT_MEMBER_SUFFIXES):
                    new_text = transform(info.filename, data.decode("utf-8", errors="replace"))
                    if new_text is not None:
                        new_data = new_text.encode("utf-8")
                        if new_data != data:
                            data = new_data
                            changed += 1

                zout.writestr(info, data)

        tmp_path.replace(output_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()

    return changed


def replace_member_text(song_path: Path, output_path: Path, replacements: dict[str, str]) -> Path:
    """Write a copy of a .song where the named members get new text content."""
    write_modified_song_copy(song_path, output_path, lambda name, _text: replacements.get(name))
    return output_path
