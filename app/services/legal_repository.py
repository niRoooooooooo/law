
import hashlib
import json
import shutil

from dataclasses import asdict
from pathlib import Path
from uuid import UUID, uuid4

from app.schemas.legal_source import (
    LegalSourceCreate,
    LegalSourceRecord,
)

from app.services.legal_ingestion import (
    ingest_legal_document,
)


# Default location: C:\Projects\law\data
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"


class LegalRepository:

    def __init__(self, data_dir=None):

        self.data_dir = Path(
            data_dir if data_dir is not None
            else DEFAULT_DATA_DIR
        )

        self.sources_dir = (
            self.data_dir / "legal_sources"
        )

        self.index_dir = (
            self.data_dir / "legal_index"
        )

        self.sources_dir.mkdir(
            parents=True, exist_ok=True
        )

        self.index_dir.mkdir(
            parents=True, exist_ok=True
        )

    # -------------------------------------
    # HELPER: VALIDATE A DOCUMENT ID
    # -------------------------------------

    def _valid_id(self, document_id):

        return str(UUID(str(document_id)))

    # -------------------------------------
    # HELPER: WRITE JSON SAFELY
    # -------------------------------------

    def _write_json(self, path, data):

        path.parent.mkdir(
            parents=True, exist_ok=True
        )

        temporary = path.with_name(
            f".{path.name}.{uuid4().hex}.tmp"
        )

        temporary.write_text(
            json.dumps(
                data,
                indent=2,
                ensure_ascii=False
            ),
            encoding="utf-8"
        )

        temporary.replace(path)

    # -------------------------------------
    # HELPER: READ CURRENT VERSION NUMBER
    # -------------------------------------

    def _current_version(self, document_id):

        document_id = self._valid_id(document_id)

        pointer = (
            self.index_dir
            / document_id
            / "current.json"
        )

        if not pointer.is_file():
            raise FileNotFoundError(
                "Legal source was not found."
            )

        data = json.loads(
            pointer.read_text(encoding="utf-8")
        )

        return data["current_version"]

    # -------------------------------------
    # HELPER: SAVE A VERSION
    # -------------------------------------

    def _save_version(
        self,
        document_id,
        version,
        source_file,
        metadata
    ):

        # Validate and extract BEFORE saving
        extraction = ingest_legal_document(
            source_file
        )

        filename = Path(source_file).name

        if metadata.is_demo:
            folder = "demo"
        elif metadata.jurisdiction == "BD":
            folder = "bangladesh"
        else:
            folder = "foreign"

        destination = (
            self.sources_dir
            / folder
            / document_id
            / f"v{version}"
            / filename
        )

        if destination.exists():
            raise FileExistsError(
                "This document version already exists."
            )

        destination.parent.mkdir(
            parents=True, exist_ok=True
        )

        shutil.copy2(
            source_file,
            destination
        )

        record = LegalSourceRecord(
            **metadata.model_dump(),
            document_id=document_id,
            version=version,
            original_filename=filename,
            stored_path=str(
                destination.relative_to(
                    self.data_dir
                ).as_posix()
            ),
            verification_status=(
                "demo"
                if metadata.is_demo
                else "pending_legal_review"
            )
        )

        # Preserve a fingerprint of the file
        file_hash = hashlib.sha256(
            destination.read_bytes()
        ).hexdigest()

        index_data = {
            "source": record.model_dump(
                mode="json"
            ),
            "sha256": file_hash,
            "total_characters": (
                extraction.total_characters
            ),
            "unreadable_pages": (
                extraction.unreadable_pages
            ),
            "sections": [
                asdict(section)
                for section in extraction.sections
            ]
        }

        index_path = (
            self.index_dir
            / document_id
            / f"v{version}.json"
        )

        self._write_json(
            index_path,
            index_data
        )

        # Activate new version only after
        # processing and indexing succeeds
        pointer = (
            self.index_dir
            / document_id
            / "current.json"
        )

        self._write_json(
            pointer,
            {"current_version": version}
        )

        return record

    # -------------------------------------
    # FUNCTION 1: ADD NEW DOCUMENT
    # -------------------------------------

    def add_document(
        self,
        file_path,
        metadata: LegalSourceCreate
    ) -> LegalSourceRecord:

        document_id = str(uuid4())

        return self._save_version(
            document_id=document_id,
            version=1,
            source_file=file_path,
            metadata=metadata
        )

    # -------------------------------------
    # FUNCTION 2: REPLACE DOCUMENT
    # -------------------------------------

    def replace_document(
        self,
        document_id,
        file_path
    ) -> LegalSourceRecord:

        previous = self.get_document(
            document_id
        )["source"]

        metadata = LegalSourceCreate(
            title=previous["title"],
            jurisdiction=previous["jurisdiction"],
            agreement_type=previous["agreement_type"],
            is_demo=previous["is_demo"]
        )

        new_version = (
            previous["version"] + 1
        )

        return self._save_version(
            document_id=self._valid_id(
                document_id
            ),
            version=new_version,
            source_file=file_path,
            metadata=metadata
        )

    # -------------------------------------
    # FUNCTION 3: GET DOCUMENT + SECTIONS
    # -------------------------------------

    def get_document(
        self,
        document_id,
        version=None
    ) -> dict:

        document_id = self._valid_id(
            document_id
        )

        current = self._current_version(
            document_id
        )

        if version is None:
            version = current

        if not isinstance(version, int) or version < 1:
            raise ValueError(
                "Invalid document version."
            )

        index_path = (
            self.index_dir
            / document_id
            / f"v{version}.json"
        )

        if not index_path.is_file():
            raise FileNotFoundError(
                "Document version was not found."
            )

        data = json.loads(
            index_path.read_text(
                encoding="utf-8"
            )
        )

        # Determine active status dynamically
        data["source"]["is_active"] = (
            version == current
        )

        return data

    # -------------------------------------
    # FUNCTION 4: LIST CURRENT DOCUMENTS
    # -------------------------------------

    def list_documents(self) -> list:

        documents = []

        for pointer in self.index_dir.glob(
            "*/current.json"
        ):

            document_id = (
                pointer.parent.name
            )

            data = self.get_document(
                document_id
            )

            record = LegalSourceRecord.model_validate(
                data["source"]
            )

            documents.append(record)

        return documents
