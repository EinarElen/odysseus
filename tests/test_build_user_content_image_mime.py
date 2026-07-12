import base64
from pathlib import Path

from src.document_processor import build_user_content


class _UploadHandler:
    def __init__(self, path: Path):
        self.path = path

    def resolve_upload(self, fid, owner=None):
        return {"path": str(self.path), "name": "screenshot", "mime": "image/png"}

    def inside_base_dir(self, path):
        return True

    def is_image_file(self, display_name, mime):
        return True

    def is_audio_file(self, display_name, mime):
        return False

    def is_document_file(self, display_name, mime):
        return False


def test_build_user_content_uses_real_image_mime_when_filename_has_no_extension(tmp_path):
    image_path = tmp_path / "upload-without-extension"
    image_path.write_bytes(b"fake-png")

    content = build_user_content("see this", ["att1"], str(tmp_path), _UploadHandler(image_path))

    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"] == "data:image/png;base64," + base64.b64encode(b"fake-png").decode()
    assert "undefined" not in content[1]["image_url"]["url"]
