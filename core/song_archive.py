from pathlib import Path
import zipfile
import shutil
from tempfile import TemporaryDirectory


def is_song_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() == ".song" and zipfile.is_zipfile(path)


def list_archive_files(song_path: Path) -> list[str]:
    with zipfile.ZipFile(song_path, "r") as z:
        return z.namelist()


def read_text_from_archive(song_path: Path, member_name: str, encoding: str = "utf-8") -> str:
    with zipfile.ZipFile(song_path, "r") as z:
        data = z.read(member_name)
    return data.decode(encoding, errors="replace")


def write_modified_song_copy(song_path: Path, output_path: Path, replacements: dict[str, str]) -> Path:
    """
    Creates a modified copy of a .song archive.

    replacements maps archive member name -> new text content.
    Members not listed in replacements are copied unchanged.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(song_path, "r") as zin, zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            if info.filename in replacements:
                zout.writestr(info, replacements[info.filename].encode("utf-8"))
            else:
                zout.writestr(info, zin.read(info.filename))

    return output_path
