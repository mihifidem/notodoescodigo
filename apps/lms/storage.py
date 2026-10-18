import os
from pathlib import Path

from django.conf import settings
from django.core.files.storage import FileSystemStorage


class PrivateMediaStorage(FileSystemStorage):
    """Storage kept outside MEDIA_ROOT so private course files are never exposed via /media/."""

    def __init__(self, location=None, *args, **kwargs):
        base_dir = Path(getattr(settings, 'BASE_DIR', Path(__file__).resolve().parents[2]))
        if location is None:
            location = base_dir / 'private_media'
        location = Path(location)
        location.mkdir(parents=True, exist_ok=True)
        super().__init__(location=str(location), *args, **kwargs)

    def url(self, name):
        raise ValueError('Private file storage does not expose a public URL.')

    def _save(self, name, content):
        directory = Path(self.location)
        directory.mkdir(parents=True, exist_ok=True)
        if name and os.path.sep in name:
            safe_name = os.path.basename(name)
            if safe_name != name:
                name = str(directory / safe_name)
        return super()._save(name, content)
