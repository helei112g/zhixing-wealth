"""Capacity and ZIP handling shared by live and offline full backups."""
import hashlib
import shutil
import zipfile

from .common import DomainError, json_dumps, now

MAX_BACKUP_BYTES = 5 * 1024 ** 3
COPY_CHUNK_BYTES = 1024 ** 2


def check_size(size, label):
    if size > MAX_BACKUP_BYTES:
        raise DomainError(f'{label}超过5 GiB')


def check_database_capacity(db):
    pages = db.execute('PRAGMA page_count').fetchone()[0]
    page_size = db.execute('PRAGMA page_size').fetchone()[0]
    check_size(pages * page_size, '数据库')


def file_sha256(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def write_archive(target, database, notes, *, schema, application):
    manifest = {
        'format': 2, 'schema': schema, 'created_at': now(), 'application': application,
        'sha256': file_sha256(database), 'note_files': {},
    }
    with zipfile.ZipFile(target, 'x', zipfile.ZIP_DEFLATED) as archive:
        # Writing a file path lets zipfile stream it and use ZIP64 when needed.
        archive.write(database, 'portfolio.sqlite')
        for name, content in notes:
            manifest['note_files'][name] = hashlib.sha256(content).hexdigest()
            archive.writestr(name, content)
        archive.writestr('manifest.json', json_dumps(manifest))
    with zipfile.ZipFile(target) as archive:
        if archive.testzip() is not None:
            raise DomainError('完整备份压缩校验失败')


def extract_database(archive, database, info):
    with archive.open(info) as source, database.open('xb') as target:
        shutil.copyfileobj(source, target, COPY_CHUNK_BYTES)
    return file_sha256(database)
