from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.utils.deconstruct import deconstructible


@deconstructible
class CertificationEvidenceStorage(FileSystemStorage):
    def __init__(self):
        location = settings.BASE_DIR / 'private_uploads' / 'certification_evidence'
        super().__init__(location=location)

    def url(self, name):
        raise ValueError('Las evidencias de certificación no tienen URL pública.')
